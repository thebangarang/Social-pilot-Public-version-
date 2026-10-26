"""Threads adapter — real publish via Threads Login (graph.threads.net).

Two-step container flow: POST /{threads-user-id}/threads (media_type TEXT /
IMAGE / VIDEO, with image_url/video_url hosted on a PUBLIC HTTPS URL Meta's
servers can fetch) then POST /{threads-user-id}/threads_publish. Replies reuse
the same container flow with `reply_to_id`.

Auth is OAuth 2.0: a short-lived token (~1h) exchanged for a ~60d long-lived
token via `grant_type=th_exchange_token`. Insights need
threads_manage_insights; replies need threads_manage_replies +
threads_read_replies.
"""
import logging
import time
import uuid

import httpx

from app.config import settings
from app.security import decrypt_secret, media_signature

logger = logging.getLogger("social_pilot.threads")

GRAPH = "https://graph.threads.net/v1.0"
MAX_TEXT = 500


class ThreadsAdapter:
    name = "threads"

    def _token(self, account):
        if account is None or not account.token_enc:
            raise RuntimeError("Compte Threads non connecté (OAuth requis).")
        return decrypt_secret(account.token_enc)

    def _threads_id(self, account, token):
        if (account.account_meta or {}).get("threads_user_id"):
            return account.account_meta["threads_user_id"]
        r = httpx.get(f"{GRAPH}/me", params={"fields": "id", "access_token": token}, timeout=30.0)
        r.raise_for_status()
        return r.json()["id"]

    def _media_url(self, publication) -> str:
        mids = publication.media_ids or []
        if not mids:
            raise RuntimeError("Threads : aucun média attaché.")
        mid = mids[0]
        sig = media_signature(mid)
        return f"{settings.public_base_url.rstrip('/')}/m/{mid}?sig={sig}"

    def _first_media_kind(self, publication) -> str | None:
        """Kind ("image"/"video") of the first resolvable media item, else None.

        Threads needs the right `media_type` + `image_url`/`video_url`, unlike
        Instagram which always passes `image_url`. Text-only posts (no media)
        are supported natively by Threads, so `None` → TEXT.
        """
        from app.config import PROJECT_ROOT
        from app.database import SessionLocal
        from app.models import MediaItem

        if not publication.media_ids:
            return None
        db = SessionLocal()
        try:
            for mid in publication.media_ids:
                try:
                    item = db.get(MediaItem, uuid.UUID(str(mid)))
                except (ValueError, TypeError):
                    continue
                if item is None:
                    continue
                if (PROJECT_ROOT / item.stored_path).exists():
                    return item.kind
            return None
        finally:
            db.close()

    # -- container helpers --------------------------------------------------
    def _create_container(self, threads_id, token, payload) -> str:
        r = httpx.post(
            f"{GRAPH}/{threads_id}/threads",
            data={**payload, "access_token": token},
            timeout=60.0,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Threads container {r.status_code}: {r.text[:200]}")
        return r.json()["id"]

    def _poll_container(self, token, container_id) -> None:
        # Meta ingests the text/image_url/video_url asynchronously: publishing
        # before it finishes fails. Poll status until FINISHED, then leave a
        # short buffer — the container can report FINISHED a moment before its
        # content is fully materialized, and publishing in that window 400s
        # with "media content not found" (subcode 4279009).
        for _ in range(30):  # ~30s max
            s = httpx.get(
                f"{GRAPH}/{container_id}",
                params={"fields": "status", "access_token": token},
                timeout=30.0,
            )
            if s.status_code == 200:
                st = (s.json().get("status") or "").upper()
                if st == "FINISHED":
                    time.sleep(3)  # let the content fully materialize
                    return
                if st in ("ERROR", "EXPIRED"):
                    raise RuntimeError(f"Threads container {st}: {s.text[:200]}")
            time.sleep(1)

    def _publish_container(self, threads_id, token, container_id) -> str:
        for attempt in range(3):
            r = httpx.post(
                f"{GRAPH}/{threads_id}/threads_publish",
                data={"creation_id": container_id, "access_token": token},
                timeout=60.0,
            )
            if r.status_code < 400:
                return r.json()["id"]
            # 4279009 = "media content not found": the container isn't ready
            # yet. Wait and retry rather than fail the whole publish.
            try:
                if r.json().get("error", {}).get("error_subcode") == 4279009 and attempt < 2:
                    time.sleep(3)
                    continue
            except Exception:  # noqa: BLE001 — non-JSON error body
                pass
            raise RuntimeError(f"Threads publish {r.status_code}: {r.text[:200]}")
        raise RuntimeError("Threads publish failed after retries")  # pragma: no cover

    # -- publish ------------------------------------------------------------
    def publish(self, publication, account=None) -> tuple[str, str]:
        token = self._token(account)
        if publication.remote_post_id:
            return publication.remote_post_id, publication.remote_url or ""

        threads_id = self._threads_id(account, token)
        caption = (publication.caption or "").strip()[:MAX_TEXT]
        kind = self._first_media_kind(publication)

        if kind == "image":
            payload = {"media_type": "IMAGE", "image_url": self._media_url(publication), "text": caption}
        elif kind == "video":
            payload = {"media_type": "VIDEO", "video_url": self._media_url(publication), "text": caption}
        else:
            payload = {"media_type": "TEXT", "text": caption}

        if not caption and kind is None:
            raise RuntimeError("Threads : texte vide (et aucun média attaché).")

        container_id = self._create_container(threads_id, token, payload)
        self._poll_container(token, container_id)
        media_id = self._publish_container(threads_id, token, container_id)

        permalink = f"https://www.threads.net/t/{media_id}"
        handle = getattr(account, "handle", "") if account else ""
        if handle:
            permalink = f"https://www.threads.net/@{handle}/post/{media_id}"
        try:
            pr = httpx.get(
                f"{GRAPH}/{media_id}",
                params={"fields": "permalink", "access_token": token},
                timeout=30.0,
            )
            if pr.status_code == 200 and pr.json().get("permalink"):
                permalink = pr.json()["permalink"]
        except Exception:  # noqa: BLE001 — permalink is best-effort
            pass
        return media_id, permalink

    # -- insights -----------------------------------------------------------
    def insights(self, publication, account=None) -> dict:
        """Media insights mapped to the app's canonical keys
        (threads_manage_insights). Threads exposes views/likes/replies/reposts/
        quotes/shares; we map replies→comments and reposts→shares. quotes and
        Threads' own (in-development) shares metric have no canonical bucket,
        so they are left out rather than fabricated.
        """
        token = self._token(account)
        if not publication.remote_post_id:
            return {}
        mid = publication.remote_post_id
        out: dict = {}
        r = httpx.get(
            f"{GRAPH}/{mid}/insights",
            params={"metric": "views,likes,replies,reposts", "access_token": token},
            timeout=30.0,
        )
        if r.status_code == 200:
            for item in (r.json().get("data") or []):
                name = item.get("name")
                values = item.get("values") or []
                if not (name and values and "value" in values[0]):
                    continue
                value = values[0]["value"]
                if name == "replies":
                    out["comments"] = value
                elif name == "reposts":
                    out["shares"] = value
                elif name in ("views", "likes"):
                    out[name] = value
        return out

    # -- comments / replies -------------------------------------------------
    def comments(self, publication, account=None) -> list[dict]:
        """Top-level replies on a published thread (threads_read_replies)."""
        token = self._token(account)
        if not publication.remote_post_id:
            return []
        r = httpx.get(
            f"{GRAPH}/{publication.remote_post_id}/replies",
            params={"fields": "id,text,username", "access_token": token},
            timeout=30.0,
        )
        if r.status_code >= 400:
            logger.warning("threads comments %s: %s", r.status_code, r.text[:200])
            return []
        out = []
        for c in (r.json().get("data") or []):
            cid = c.get("id", "")
            if cid:
                out.append({
                    "remote_comment_id": cid,
                    "author_name": c.get("username", ""),
                    "text": c.get("text", ""),
                })
        return out

    def reply(self, comment, account=None) -> str:
        """Post a reply via the publish flow with `reply_to_id`
        (threads_manage_replies)."""
        token = self._token(account)
        remote_id = getattr(comment, "remote_comment_id", None)
        if not remote_id:
            raise RuntimeError("Réponse sans identifiant distant.")
        text = (getattr(comment, "reply_draft", "") or "").strip()
        if not text:
            raise RuntimeError("Réponse vide.")
        threads_id = self._threads_id(account, token)
        container_id = self._create_container(
            threads_id, token,
            {"media_type": "TEXT", "text": text[:MAX_TEXT], "reply_to_id": remote_id},
        )
        self._poll_container(token, container_id)
        return self._publish_container(threads_id, token, container_id)

    def delete(self, publication, account=None) -> None:
        """Delete a published thread (threads_content_publish)."""
        token = self._token(account)
        mid = publication.remote_post_id
        if not mid:
            return
        r = httpx.delete(f"{GRAPH}/{mid}", params={"access_token": token}, timeout=30.0)
        if r.status_code >= 400 and r.status_code != 404:
            raise RuntimeError(f"Threads delete {r.status_code}: {r.text[:200]}")
