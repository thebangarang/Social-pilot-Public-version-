"""Instagram adapter — real publish via Instagram Login (graph.instagram.com).

Two-step container flow: POST /{ig_id}/media (image_url/video_url must be a
PUBLIC HTTPS URL Meta's servers can fetch) then POST /{ig_id}/media_publish.
Auth is OAuth 2.0 (short-lived token exchanged for a ~60d long-lived token).
Comments/insights need instagram_business_manage_* scopes — phase 2.
"""
import logging
import time

import httpx

from app.config import settings
from app.security import decrypt_secret, media_signature

logger = logging.getLogger("social_pilot.instagram")

GRAPH = "https://graph.instagram.com/v26.0"


class InstagramAdapter:
    name = "instagram"

    def _token(self, account):
        if account is None or not account.token_enc:
            raise RuntimeError("Compte Instagram non connecté (OAuth requis).")
        return decrypt_secret(account.token_enc)

    def _ig_id(self, account, token):
        if (account.account_meta or {}).get("ig_user_id"):
            return account.account_meta["ig_user_id"]
        r = httpx.get(f"{GRAPH}/me", params={"fields": "id", "access_token": token}, timeout=30.0)
        r.raise_for_status()
        return r.json()["id"]

    def _media_url(self, publication) -> str:
        mids = publication.media_ids or []
        if not mids:
            raise RuntimeError("Instagram requiert un média (image/vidéo) — pas de post texte seul.")
        # Public capability URL: signed so Meta can fetch it without auth, but a
        # leaked internal id cannot be forged into a working URL.
        mid = mids[0]
        sig = media_signature(mid)
        return f"{settings.public_base_url.rstrip('/')}/m/{mid}?sig={sig}"

    def publish(self, publication, account=None) -> tuple[str, str]:
        token = self._token(account)
        if publication.remote_post_id:
            return publication.remote_post_id, publication.remote_url or ""

        ig_id = self._ig_id(account, token)
        r = httpx.post(
            f"{GRAPH}/{ig_id}/media",
            data={
                "image_url": self._media_url(publication),
                "caption": (publication.caption or "").strip()[:2200],
                "access_token": token,
            },
            timeout=60.0,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Instagram media container {r.status_code}: {r.text[:200]}")
        container_id = r.json()["id"]

        # Meta ingests the image_url asynchronously: publishing before it finishes
        # fails with "Media ID is not available" (code 9007, subcode 2207027).
        # Poll the container status until FINISHED (or ERROR).
        for _ in range(20):  # ~20s max
            s = httpx.get(
                f"{GRAPH}/{container_id}",
                params={"fields": "status_code", "access_token": token},
                timeout=30.0,
            )
            if s.status_code == 200:
                sc = s.json().get("status_code")
                if sc == "FINISHED":
                    break
                if sc == "ERROR":
                    raise RuntimeError(f"Instagram media container ERROR: {s.text[:200]}")
            time.sleep(1)

        r = httpx.post(
            f"{GRAPH}/{ig_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=60.0,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Instagram media_publish {r.status_code}: {r.text[:200]}")
        media_id = r.json()["id"]
        permalink = f"https://www.instagram.com/p/{media_id}/"
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

    # -- insights / comments ---------------------------------------------
    def insights(self, publication, account=None) -> dict:
        """Media insights + engagement counts (instagram_business_manage_insights).

        For FEED media the Media Insights API does NOT support the `impressions`
        metric (400: "does not support the impressions metric for this media
        product type") — and one unsupported metric fails the whole request, so
        it is left out. Supported metrics mapped to the app's keys: reach, saved
        (→ saves), shares, views, likes, comments. Engagement counts are also
        read from the media object as a reliable fallback. impressions stays
        absent (shown as 0) — honest partial metrics, like Bluesky.
        """
        token = self._token(account)
        if not publication.remote_post_id:
            return {}
        mid = publication.remote_post_id
        out: dict = {}
        r = httpx.get(
            f"{GRAPH}/{mid}/insights",
            params={
                "metric": "reach,saved,shares,views,likes,comments",
                "access_token": token,
            },
            timeout=30.0,
        )
        if r.status_code == 200:
            for item in (r.json().get("data") or []):
                name = item.get("name")
                values = item.get("values") or []
                if name and values and "value" in values[0]:
                    key = "saves" if name == "saved" else name
                    out[key] = values[0]["value"]
        r = httpx.get(
            f"{GRAPH}/{mid}",
            params={"fields": "like_count,comments_count", "access_token": token},
            timeout=30.0,
        )
        if r.status_code == 200:
            body = r.json()
            out["likes"] = body.get("like_count", 0)
            out["comments"] = body.get("comments_count", 0)
        return out

    def reply(self, comment, account=None) -> str:
        """Post a reply to a comment (instagram_business_manage_comments)."""
        token = self._token(account)
        if not getattr(comment, "remote_comment_id", None):
            raise RuntimeError("Commentaire sans identifiant distant.")
        text = (getattr(comment, "reply_draft", "") or "").strip()
        if not text:
            raise RuntimeError("Réponse vide.")
        r = httpx.post(
            f"{GRAPH}/{comment.remote_comment_id}/replies",
            data={"message": text[:2200], "access_token": token},
            timeout=30.0,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Instagram reply {r.status_code}: {r.text[:200]}")
        return r.json().get("id", "")

    def comments(self, publication, account=None) -> list[dict]:
        """Top-level comments on a published media (instagram_business_manage_comments)."""
        token = self._token(account)
        if not publication.remote_post_id:
            return []
        r = httpx.get(
            f"{GRAPH}/{publication.remote_post_id}/comments",
            params={"fields": "id,text,username", "access_token": token},
            timeout=30.0,
        )
        if r.status_code >= 400:
            logger.warning("instagram comments %s: %s", r.status_code, r.text[:200])
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

    def delete(self, publication, account=None) -> None:
        """Delete a published media (instagram_content_publish)."""
        token = self._token(account)
        mid = publication.remote_post_id
        if not mid:
            return
        r = httpx.delete(f"{GRAPH}/{mid}", params={"access_token": token}, timeout=30.0)
        if r.status_code >= 400 and r.status_code != 404:
            raise RuntimeError(f"Instagram delete {r.status_code}: {r.text[:200]}")
