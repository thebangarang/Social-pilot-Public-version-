"""Data model — single-tenant (one instance = one client), SQLite.

See docs/architecture.md. This module starts with the auth + theme tables used by
Phase 1; the production tables (workflows, publications, comments, metrics, ...)
are added in later phases without dropping anything.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Operator(Base):
    """A user of this Social Pilot instance (the client's team).

    role: "admin" (manages users) | "operator" (production team member).
    TOTP secret is encrypted at rest (Fernet); totp_enabled gates the 2FA step.
    """

    __tablename__ = "operators"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="operator")  # admin | operator
    can_publish: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # admin-granted publish capability
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)  # en | fr | ...
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-encrypted
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserSession(Base):
    """Server-side login session; the cookie holds the raw token, we store its hash."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    operator_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LoginAttempt(Base):
    """Append-only login audit (email + ip + success) — powers account lockout."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Theme(Base):
    """Whitelabel theme preset (neutral/minimalist by default)."""

    __tablename__ = "themes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # slug
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    bg: Mapped[str] = mapped_column(String(16), default="#0f1115")
    surface: Mapped[str] = mapped_column(String(16), default="#171a21")
    border: Mapped[str] = mapped_column(String(16), default="#262a33")
    fg: Mapped[str] = mapped_column(String(16), default="#e6e8ee")
    muted: Mapped[str] = mapped_column(String(16), default="#8a8f9c")
    accent: Mapped[str] = mapped_column(String(16), default="#5b8cff")
    accent_2: Mapped[str] = mapped_column(String(16), default="#8a5bff")
    success: Mapped[str] = mapped_column(String(16), default="#3fbf7f")
    warning: Mapped[str] = mapped_column(String(16), default="#e0a33c")
    danger: Mapped[str] = mapped_column(String(16), default="#e05b5b")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Brand(Base):
    """A brand managed by this client — the entity that groups social accounts,
    branding kit and content (whitelabel multi-brand)."""

    __tablename__ = "brands"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    logo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # standardized 256px fit icon
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectedAccount(Base):
    """A social platform account connected via OAuth (or the mock adapter)."""

    __tablename__ = "connected_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # tiktok|instagram|facebook
    handle: Mapped[str] = mapped_column(String(120), nullable=False)
    account_meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-encrypted
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMSetting(Base):
    """The instance's LLM / media provider configuration (singleton row)."""

    __tablename__ = "llm_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(40), default="openrouter")
    base_url: Mapped[str] = mapped_column(String(255), default="https://openrouter.ai/api/v1")
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-encrypted
    model: Mapped[str] = mapped_column(String(120), default="anthropic/claude-sonnet-4")
    image_model: Mapped[str] = mapped_column(String(120), default="google/gemini-2.5-flash-image")
    video_model: Mapped[str] = mapped_column(String(120), default="google/veo-3.1")
    monthly_limit: Mapped[float | None] = mapped_column(Float, nullable=True)  # USD, soft cap
    topup_mode: Mapped[str] = mapped_column(String(20), default="manual")  # manual | auto
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UsageRecord(Base):
    """A single LLM / media generation cost event (the billing-grade ledger).

    Priced through ``app.usage_meter``: the split columns + ``price_source`` are
    what let a statement prove where each number came from. ``cost`` keeps its
    original meaning (USD, as reported by the provider when it reports one).
    ``price_source``: provider | pinned | unknown — ``unknown`` means the row
    must not be billed yet.
    """

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # llm | media
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tokens: Mapped[int | None] = mapped_column(nullable=True)
    cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_cached: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # provider|pinned|unknown
    price_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)  # USD
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ChatSession(Base):
    """A persistent generation conversation (workspace, not a throwaway prompt)."""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    """One message in a chat session (user / assistant / system)."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    media_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    branding_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # per-post branding instructions
    media_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # image | video | None (auto)
    on_media_text: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {mode: auto|no|yes, text}
    media_brief: Mapped[str | None] = mapped_column(Text, nullable=True)  # user's media-dedicated prompt
    platforms: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # target platforms
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MediaItem(Base):
    """An ingested media file, transcoded to a standard format."""

    __tablename__ = "media_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # image | video | audio | doc | other
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(255), nullable=False)  # relative to project root
    mime: Mapped[str | None] = mapped_column(String(120), nullable=True)
    width: Mapped[int | None] = mapped_column(nullable=True)
    height: Mapped[int | None] = mapped_column(nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    thumb_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # video thumbnail
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Provenance of an asset the generator produced (AI Act Article 50(2)). A row created by
    # app/services/pipeline.generate_media is `ai_generated=True` and stays `ai_marking="none"` until
    # the durable mark is applied to the pixels; the publish path refuses to send an unmarked generated
    # asset to a client's account, so the duty is held rather than described while it is owed.
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False,
                                               server_default="0")
    ai_marking: Mapped[str] = mapped_column(String(20), default="none", nullable=False,
                                            server_default="none")  # none | watermark | c2pa
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BrandAsset(Base):
    """An element of the user's default branding kit (logo, colors, fonts, ...)."""

    __tablename__ = "brand_assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # logo|color|font|visual|guideline|other
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("media_items.id", ondelete="SET NULL"), nullable=True
    )
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True
    )
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StylePreset(Base):
    """An art-direction style preset (built-in or refined by the user)."""

    __tablename__ = "style_presets"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # slug
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    directives: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Proposal(Base):
    """A numbered post proposal generated by the agent."""

    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)  # caption, hashtags, hook, cta, media_prompt
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active|superseded|validated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Post(Base):
    """A validated proposal that has entered the production pipeline."""

    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True
    )
    brand_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brands.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)  # caption, hashtags, hook, cta, media_prompt, ...
    media_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [uuid str] generated media
    media_variants: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {platform: media_id}
    source_media_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # media attached to the original request
    branding_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # per-post branding consignes
    media_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # image | video | text | None (auto)
    on_media_text: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {mode: auto|no|yes, text}
    media_brief: Mapped[str | None] = mapped_column(Text, nullable=True)  # user's media-dedicated prompt
    compliance: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {verdict, risks}
    # concept_validated → media_ready → compliance_ok → approved → scheduled|published | failed
    status: Mapped[str] = mapped_column(String(30), default="concept_validated", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Publication(Base):
    """A per-network publication derived from a post."""

    __tablename__ = "publications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)  # tiktok | instagram | facebook
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connected_accounts.id", ondelete="SET NULL"), nullable=True
    )
    caption: Mapped[str] = mapped_column(Text, default="", nullable=False)
    media_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # draft | scheduled | ready | publishing | published | failed
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remote_post_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    remote_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), default=lambda: uuid.uuid4().hex, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MetricSnapshot(Base):
    """One metric value for one publication at one capture time (time series).

    One poll = one row per metric key, timestamped — never overwritten, so we keep
    the history (see docs/analytics-model.md). Derived metrics are computed on the
    fly, not stored.
    """

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Comment(Base):
    """A comment on a published post, triaged for the community-manager inbox."""

    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    remote_comment_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    author_name: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(20), default="green", nullable=False)  # green|orange|red
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)  # new|replied|skipped
    reply_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
