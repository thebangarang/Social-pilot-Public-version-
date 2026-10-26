"""Chat sessions + message send (agent generation) + proposal adjust/validate."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import generate_adjustment, generate_proposals
from app.auth import utcnow
from app.deps import get_current_operator, get_db
from app.models import ChatMessage, ChatSession, MediaItem, Operator, Post, Proposal, StylePreset
from app.schemas import (
    AdjustProposalIn,
    AdjustProposalOut,
    ChatMessageOut,
    ChatSessionOut,
    ProposalOut,
    SendMessageIn,
    SendMessageOut,
    ValidateProposalIn,
    ValidateProposalOut,
)

router = APIRouter(prefix="/chat", tags=["chat"])


def _get_session(db: Session, session_id: uuid.UUID, operator: Operator) -> ChatSession:
    # Single-tenant team: any member can open any session (owner_id is just the creator).
    s = db.get(ChatSession, session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Session introuvable")
    return s


def _history(db: Session, session_id: uuid.UUID) -> list[dict]:
    msgs = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(12)
    ).all()
    return [{"role": m.role, "content": m.content} for m in reversed(msgs)]


def _last_user_message(db: Session, session_id: uuid.UUID, before_message_id: int) -> ChatMessage | None:
    """The user request that triggered a given assistant message."""
    return db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role == "user",
            ChatMessage.id < before_message_id,
        )
        .order_by(ChatMessage.id.desc())
        .limit(1)
    ).first()


@router.get("/sessions", response_model=list[ChatSessionOut])
def list_sessions(db: Session = Depends(get_db), operator: Operator = Depends(get_current_operator)):
    return db.scalars(
        select(ChatSession)
        .where(ChatSession.archived.is_(False))
        .order_by(ChatSession.updated_at.desc())
    ).all()


@router.post("/sessions", response_model=ChatSessionOut, status_code=201)
def create_session(db: Session = Depends(get_db), operator: Operator = Depends(get_current_operator)):
    s = ChatSession(owner_id=operator.id, title="")
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/sessions/{session_id}", response_model=list[ChatMessageOut])
def get_messages(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    _get_session(db, session_id, operator)
    return db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
    ).all()


@router.get("/sessions/{session_id}/proposals", response_model=list[ProposalOut])
def get_proposals(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    _get_session(db, session_id, operator)
    return db.scalars(
        select(Proposal)
        .where(Proposal.session_id == session_id, Proposal.status == "active")
        .order_by(Proposal.number)
    ).all()


@router.post("/sessions/{session_id}/send", response_model=SendMessageOut)
def send_message(
    session_id: uuid.UUID,
    payload: SendMessageIn,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    session = _get_session(db, session_id, operator)
    preset = None
    if payload.preset_id:
        preset = db.get(StylePreset, payload.preset_id)
        if preset is None:
            raise HTTPException(status_code=400, detail="Preset introuvable")

    user_msg = ChatMessage(
        session_id=session.id,
        role="user",
        content=payload.content,
        media_ids=[str(m) for m in payload.media_ids],
        branding_usage=payload.branding_usage,
        media_type=payload.media_type,
        on_media_text=payload.on_media_text,
        media_brief=payload.media_brief,
        platforms=list(payload.platforms),
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    if payload.media_ids:
        for m in db.scalars(
            select(MediaItem).where(MediaItem.id.in_(payload.media_ids))
        ).all():
            m.usage_count += 1
        db.commit()

    if not session.title:
        session.title = payload.content[:60] + ("…" if len(payload.content) > 60 else "")

    history = _history(db, session.id)
    try:
        proposals_data = generate_proposals(db, payload.content, preset, history, payload.branding_usage, payload.media_type, payload.on_media_text, payload.media_brief, list(payload.platforms), operator.language)
    except Exception as e:  # noqa: BLE001 — surface a clean error to the user
        raise HTTPException(status_code=502, detail=f"Génération échouée : {e}")

    asst_msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=(
            f"{len(proposals_data)} proposition(s) générée(s)."
            if operator.language == "fr"
            else f"{len(proposals_data)} proposal(s) generated."
        ),
    )
    db.add(asst_msg)
    session.updated_at = utcnow()
    db.flush()  # get asst_msg.id
    rows = []
    for p in proposals_data:
        row = Proposal(
            session_id=session.id, message_id=asst_msg.id,
            number=p["number"], content=p, status="active",
        )
        db.add(row)
        rows.append(row)
    db.commit()
    db.refresh(asst_msg)
    for r in rows:
        db.refresh(r)
    return SendMessageOut(user_message=user_msg, assistant_message=asst_msg, proposals=rows)


@router.post("/proposals/{proposal_id}/adjust", response_model=AdjustProposalOut)
def adjust_proposal(
    proposal_id: uuid.UUID,
    payload: AdjustProposalIn,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    proposal = db.get(Proposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposition introuvable")
    session = _get_session(db, proposal.session_id, operator)
    user_msg = _last_user_message(db, session.id, proposal.message_id)
    request = user_msg.content if user_msg else ""
    branding = user_msg.branding_usage if user_msg else None
    media_type = user_msg.media_type if user_msg else None
    on_media_text = user_msg.on_media_text if user_msg else None
    media_brief = user_msg.media_brief if user_msg else None
    platforms = user_msg.platforms if user_msg else None

    try:
        proposals_data = generate_adjustment(
            db, request, proposal.content, payload.feedback,
            history=_history(db, session.id), branding_usage=branding,
            media_type=media_type, on_media_text=on_media_text,
            media_brief=media_brief, platforms=platforms,
            language=operator.language,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Génération échouée : {e}")

    for p in db.scalars(
        select(Proposal).where(Proposal.session_id == session.id, Proposal.status == "active")
    ).all():
        p.status = "superseded"

    asst_msg = ChatMessage(
        session_id=session.id, role="assistant",
        content=(
            "Ajustement — proposition retravaillée."
            if operator.language == "fr"
            else "Adjustment — proposal reworked."
        ),
    )
    db.add(asst_msg)
    session.updated_at = utcnow()
    db.flush()
    rows = []
    for p in proposals_data:
        row = Proposal(
            session_id=session.id, message_id=asst_msg.id,
            number=p["number"], content=p, status="active",
        )
        db.add(row)
        rows.append(row)
    db.commit()
    db.refresh(asst_msg)
    for r in rows:
        db.refresh(r)
    return AdjustProposalOut(assistant_message=asst_msg, proposals=rows)


@router.post("/proposals/{proposal_id}/validate", response_model=ValidateProposalOut)
def validate_proposal(
    proposal_id: uuid.UUID,
    payload: ValidateProposalIn | None = None,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    proposal = db.get(Proposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposition introuvable")
    _get_session(db, proposal.session_id, operator)
    proposal.status = "validated"
    for p in db.scalars(
        select(Proposal).where(
            Proposal.session_id == proposal.session_id,
            Proposal.status == "active",
            Proposal.id != proposal.id,
        )
    ).all():
        p.status = "superseded"
    # Enter the production pipeline: create a post in "concept_validated" state.
    src = _last_user_message(db, proposal.session_id, proposal.message_id)
    post = Post(
        owner_id=operator.id,
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        title=str((proposal.content or {}).get("caption", ""))[:160],
        content=proposal.content,
        media_ids=[],
        source_media_ids=list(src.media_ids if src else []),
        branding_usage=src.branding_usage if src else None,
        media_type=src.media_type if src else None,
        on_media_text=src.on_media_text if src else None,
        media_brief=src.media_brief if src else None,
        status="concept_validated",
        brand_id=payload.brand_id if payload else None,
    )
    db.add(post)
    db.commit()
    db.refresh(proposal)
    db.refresh(post)
    return ValidateProposalOut(proposal=proposal, post=post)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Delete a session (cascades to its messages + proposals)."""
    session = _get_session(db, session_id, operator)
    db.delete(session)
    db.commit()
