"""Bluesky adapter — real publish / insights / comments over the AT Protocol.

Auth uses the account's *App Password* (a scoped, revocable password created in
Bluesky settings). It is stored encrypted at rest and we call `createSession`
per operation — the App Password is the durable secret, so there is no
refresh-token bookkeeping. See docs/platform-accounts-checklist.md.
"""
import datetime
import logging
import uuid
from pathlib import Path

import httpx

from app.media import MarkingLost, fit_for_platform
from app.security import decrypt_secret

logger = logging.getLogger("social_pilot.bluesky")

PDS = "https://bsky.social"
MAX_TEXT_GRAPHEMES = 300
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 1_000_000


class BlueskyAdapter:
    name = "bluesky"

    # -- credentials -------------------------------------------------------
    def _cred(self, account):
        if account is None or not account.token_enc:
            raise RuntimeError("Compte Bluesky non connecté (handle + App Password requis).")
        return account.handle, decrypt_secret(account.token_enc)

    def _session(self, handle: str, app_password: str) -> tuple[str, str]:
        r = httpx.post(
            f"{PDS}/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": app_password},
            timeout=30.0,
        )
        if r.status_code == 401:
            raise RuntimeError("Bluesky : identifiant ou App Password invalide.")
        r.raise_for_status()
        data = r.json()
        return data["accessJwt"], data.get("did", "")

    # -- publish -----------------------------------------------------------
    def publish(self, publication, account=None) -> tuple[str, str]:
        # Idempotency: a retry that already succeeded returns the same post
        # without hitting the network.
        if publication.remote_post_id:
            handle = account.handle if account is not None else "?"
            rkey = publication.remote_post_id.rsplit("/", 1)[-1]
            return publication.remote_post_id, f"https://bsky.app/profile/{handle}/post/{rkey}"

        handle, app_password = self._cred(account)
        access_jwt, did = self._session(handle, app_password)

        images = self._upload_images(access_jwt, publication)
        record = {
            "text": (publication.caption or "").strip()[:MAX_TEXT_GRAPHEMES],
            "createdAt": datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "langs": ["fr"],
        }
        if images:
            record["embed"] = {
                "$type": "app.bsky.embed.images",
                "images": [{"image": blob, "alt": ""} for blob in images],
            }
        r = httpx.post(
            f"{PDS}/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {access_jwt}"},
            json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
            timeout=30.0,
        )
        r.raise_for_status()
        uri = r.json()["uri"]  # at://did/app.bsky.feed.post/<rkey>
        rkey = uri.rsplit("/", 1)[-1]
        return uri, f"https://bsky.app/profile/{handle}/post/{rkey}"

    def _upload_images(self, access_jwt, publication) -> list:
        from app.config import PROJECT_ROOT
        from app.database import SessionLocal
        from app.models import MediaItem

        blobs = []
        db = SessionLocal()
        try:
            for mid in (publication.media_ids or [])[:MAX_IMAGES]:
                try:
                    item = db.get(MediaItem, uuid.UUID(str(mid)))
                except (ValueError, TypeError):
                    continue
                if item is None or item.kind != "image":
                    continue
                path = PROJECT_ROOT / item.stored_path
                if not path.exists():
                    continue
                try:
                    data, mime = fit_for_platform(path, "bluesky", item.mime,
                                                  ai_generated=bool(item.ai_generated))
                except MarkingLost as e:
                    # Fail closed, and *visibly*: this used to log and `continue`, which published the post
                    # without its image while the run reported success — a silent degradation of the thing the
                    # post exists for. Publishing unmarked AI content is the Article 50(2) exposure; publishing
                    # a post whose image is missing without saying so is a different way of being wrong. So the
                    # publication fails, with the reason.
                    logger.error("bluesky: refusing to publish — an AI-generated image cannot be marked for "
                                 "this platform: %s", e)
                    raise
                except Exception as e:  # noqa: BLE001 — drop the image, keep the post
                    logger.warning("bluesky: skipping image %s (compress failed: %s)", mid, e)
                    continue
                if len(data) > MAX_IMAGE_BYTES:
                    logger.warning("bluesky: skipping image %s (still over 1MB after compress)", mid)
                    continue
                r = httpx.post(
                    f"{PDS}/xrpc/com.atproto.repo.uploadBlob",
                    headers={
                        "Authorization": f"Bearer {access_jwt}",
                        "Content-Type": mime,
                    },
                    content=data,
                    timeout=60.0,
                )
                r.raise_for_status()
                blobs.append(r.json()["blob"])
        finally:
            db.close()
        return blobs

    # -- insights ----------------------------------------------------------
    def insights(self, publication, account=None) -> dict:
        handle, app_password = self._cred(account)
        access_jwt, _ = self._session(handle, app_password)
        uri = publication.remote_post_id
        if not uri:
            return {}
        r = httpx.get(
            f"{PDS}/xrpc/app.bsky.feed.getPosts",
            params={"uris": [uri]},
            headers={"Authorization": f"Bearer {access_jwt}"},
            timeout=30.0,
        )
        r.raise_for_status()
        posts = r.json().get("posts") or []
        if not posts:
            return {}
        p = posts[0]
        # Bluesky does not expose impressions/reach/views via the public API —
        # only engagement counts. Honest, partial metrics (see docs).
        return {
            "likes": p.get("likeCount", 0),
            "comments": p.get("replyCount", 0),
            "shares": p.get("repostCount", 0),
        }

    # -- comments ----------------------------------------------------------
    def comments(self, publication, account=None) -> list[dict]:
        handle, app_password = self._cred(account)
        access_jwt, _ = self._session(handle, app_password)
        uri = publication.remote_post_id
        if not uri:
            return []
        r = httpx.get(
            f"{PDS}/xrpc/app.bsky.feed.getPostThread",
            params={"uri": uri, "depth": 1},
            headers={"Authorization": f"Bearer {access_jwt}"},
            timeout=30.0,
        )
        r.raise_for_status()
        thread = r.json().get("thread") or {}
        out = []
        for reply in (thread.get("replies") or []):
            post = reply.get("post") or {}
            author = post.get("author") or {}
            rec = post.get("record") or {}
            out.append({
                "remote_comment_id": post.get("uri", ""),
                "author_name": author.get("handle", "?") or "?",
                "text": rec.get("text", "") or "",
            })
        return out

    def delete(self, publication, account=None) -> None:
        """Delete a published post via com.atproto.repo.deleteRecord."""
        handle, app_password = self._cred(account)
        access_jwt, did = self._session(handle, app_password)
        uri = publication.remote_post_id
        if not uri:
            return
        rkey = uri.rsplit("/", 1)[-1]
        r = httpx.post(
            f"{PDS}/xrpc/com.atproto.repo.deleteRecord",
            headers={"Authorization": f"Bearer {access_jwt}"},
            json={"repo": did, "collection": "app.bsky.feed.post", "rkey": rkey},
            timeout=30.0,
        )
        r.raise_for_status()
