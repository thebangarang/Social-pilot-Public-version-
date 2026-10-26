"""Facebook Pages adapter — real publish via the Facebook Graph API (graph.facebook.com).

Unlike Instagram/Threads there is no two-step container flow: posts go straight
to `POST /{page-id}/feed` (text), `POST /{page-id}/photos` (image via a public
`url`) or `POST /{page-id}/videos` (video via a public `file_url`). Comments are
read from `/{post-id}/comments` and replies are `POST /{comment-id}/comments`.

Auth is a Facebook Login user token exchanged for a long-lived token, then a
Page access token resolved via `GET /me/accounts`. The account stores the PAGE
access token in token_enc (what publish uses), page_id/page_name/user_id in
account_meta, and the long-lived USER token in refresh_token_enc so the page
list can be re-resolved on reconnect.
"""
import logging
import uuid

import httpx

from app.config import settings
from app.security import decrypt_secret, media_signature

logger = logging.getLogger("social_pilot.facebook")

GRAPH = "https://graph.facebook.com/v26.0"
MAX_TEXT = 5000


class FacebookAdapter:
    name = "facebook"

    def _token(self, account):
        if account is None or not account.token_enc:
            raise RuntimeError("Page Facebook non connectée (OAuth requis).")
        return decrypt_secret(account.token_enc)

    def _page_id(self, account):
        pid = (account.account_meta or {}).get("page_id")
        if not pid:
            raise RuntimeError("Page Facebook sans page_id (reconnectez la page).")
        return pid

    def _media_url(self, publication) -> str:
        mids = publication.media_ids or []
        if not mids:
            raise RuntimeError("Facebook : aucun média attaché.")
        mid = mids[0]
        sig = media_signature(mid)
        return f"{settings.public_base_url.rstrip('/')}/m/{mid}?sig={sig}"

    def _first_media_kind(self, publication) -> str | None:
        """Kind ("image"/"video") of the first resolvable media item, else None.

        Facebook supports text-only posts, so None → text post via /feed.
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

    # -- publish ------------------------------------------------------------
    def publish(self, publication, account=None) -> tuple[str, str]:
        token = self._token(account)
        if publication.remote_post_id:
            return publication.remote_post_id, publication.remote_url or ""

        page_id = self._page_id(account)
        caption = (publication.caption or "").strip()[:MAX_TEXT]
        kind = self._first_media_kind(publication)

        if kind == "image":
            r = httpx.post(
                f"{GRAPH}/{page_id}/photos",
                data={"url": self._media_url(publication), "message": caption, "access_token": token},
                timeout=60.0,
            )
        elif kind == "video":
            r = httpx.post(
                f"{GRAPH}/{page_id}/videos",
                data={"file_url": self._media_url(publication), "description": caption, "access_token": token},
                timeout=120.0,
            )
        else:
            if not caption:
                raise RuntimeError("Facebook : texte vide (et aucun média attaché).")
            r = httpx.post(
                f"{GRAPH}/{page_id}/feed",
                data={"message": caption, "access_token": token},
                timeout=60.0,
            )

        if r.status_code >= 400:
            raise RuntimeError(f"Facebook publish {r.status_code}: {r.text[:200]}")

        body = r.json()
        # /feed returns {id: "<page>_<post>"}; /photos returns {id, post_id}.
        post_id = body.get("post_id") or body.get("id")
        permalink = f"https://www.facebook.com/{post_id}"
        try:
            pr = httpx.get(
                f"{GRAPH}/{post_id}",
                params={"fields": "permalink_url", "access_token": token},
                timeout=30.0,
            )
            if pr.status_code == 200 and pr.json().get("permalink_url"):
                permalink = pr.json()["permalink_url"]
        except Exception:  # noqa: BLE001 — permalink is best-effort
            pass
        return post_id, permalink

    # -- insights -----------------------------------------------------------
    def insights(self, publication, account=None) -> dict:
        """Post insights + engagement counts, mapped to the app's canonical keys.

        Facebook post insights expose impressions (post_impressions), reach
        (post_impressions_unique) and video views (post_video_views). Reactions
        and comments are read from the post object's summary totals — more
        reliable than the reaction-by-type breakdown. shares/saves are not
        exposed for posts, so they stay absent (rendered "—").
        """
        token = self._token(account)
        if not publication.remote_post_id:
            return {}
        pid = publication.remote_post_id
        out: dict = {}
        r = httpx.get(
            f"{GRAPH}/{pid}/insights",
            params={
                "metric": "post_impressions,post_impressions_unique,post_video_views",
                "access_token": token,
            },
            timeout=30.0,
        )
        if r.status_code == 200:
            for item in (r.json().get("data") or []):
                name = item.get("name")
                values = item.get("values") or []
                if not (name and values and "value" in values[0]):
                    continue
                value = values[0]["value"]
                if name == "post_impressions":
                    out["impressions"] = value
                elif name == "post_impressions_unique":
                    out["reach"] = value
                elif name == "post_video_views":
                    out["views"] = value
        r = httpx.get(
            f"{GRAPH}/{pid}",
            params={"fields": "reactions.summary(true),comments.summary(true)", "access_token": token},
            timeout=30.0,
        )
        if r.status_code == 200:
            body = r.json()
            reactions = (body.get("reactions") or {}).get("summary", {}).get("total_count")
            comments = (body.get("comments") or {}).get("summary", {}).get("total_count")
            if reactions is not None:
                out["likes"] = reactions
            if comments is not None:
                out["comments"] = comments
        return out

    # -- comments / replies -------------------------------------------------
    def comments(self, publication, account=None) -> list[dict]:
        """Top-level comments on a published post (pages_read_engagement /
        pages_read_user_content)."""
        token = self._token(account)
        if not publication.remote_post_id:
            return []
        r = httpx.get(
            f"{GRAPH}/{publication.remote_post_id}/comments",
            params={"fields": "id,message,from", "access_token": token},
            timeout=30.0,
        )
        if r.status_code >= 400:
            logger.warning("facebook comments %s: %s", r.status_code, r.text[:200])
            return []
        out = []
        for c in (r.json().get("data") or []):
            cid = c.get("id", "")
            if cid:
                out.append({
                    "remote_comment_id": cid,
                    "author_name": (c.get("from") or {}).get("name", ""),
                    "text": c.get("message", ""),
                })
        return out

    def reply(self, comment, account=None) -> str:
        """Post a reply to a comment (pages_manage_engagement)."""
        token = self._token(account)
        remote_id = getattr(comment, "remote_comment_id", None)
        if not remote_id:
            raise RuntimeError("Réponse sans identifiant distant.")
        text = (getattr(comment, "reply_draft", "") or "").strip()
        if not text:
            raise RuntimeError("Réponse vide.")
        r = httpx.post(
            f"{GRAPH}/{remote_id}/comments",
            data={"message": text[:MAX_TEXT], "access_token": token},
            timeout=30.0,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Facebook reply {r.status_code}: {r.text[:200]}")
        return r.json().get("id", "")

    def delete(self, publication, account=None) -> None:
        """Delete a published post (pages_manage_posts)."""
        token = self._token(account)
        pid = publication.remote_post_id
        if not pid:
            return
        r = httpx.delete(f"{GRAPH}/{pid}", params={"access_token": token}, timeout=30.0)
        if r.status_code >= 400 and r.status_code != 404:
            raise RuntimeError(f"Facebook delete {r.status_code}: {r.text[:200]}")
