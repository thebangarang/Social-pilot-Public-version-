"""Pinterest adapter — real publish via the Pinterest API v5 (api.pinterest.com).

Pins are image-only: every Pin needs a board + an image (no text-only posts).
Publish is a single POST /pins with media_source.image_url (public HTTPS URL).
Insights map IMPRESSION→impressions, SAVE→saves, PIN_CLICK→views; comments/reply
are not exposed via the standard scopes, so they are honest stubs.

Auth is OAuth 2.0 (authorization code, Basic auth app_id:app_secret on the token
exchange). The account stores the token, and the first board (id + name) the user
owns, resolved at connect time.
"""
import logging

import httpx

from app.config import settings
from app.security import decrypt_secret, media_signature

logger = logging.getLogger("social_pilot.pinterest")

API = "https://api.pinterest.com/v5"
MAX_TITLE = 100
MAX_DESC = 500


class PinterestAdapter:
    name = "pinterest"

    def _token(self, account):
        if account is None or not account.token_enc:
            raise RuntimeError("Compte Pinterest non connecté (OAuth requis).")
        return decrypt_secret(account.token_enc)

    def _board_id(self, account) -> str:
        bid = (account.account_meta or {}).get("board_id")
        if not bid:
            raise RuntimeError("Pinterest : aucun tableau sélectionné (reconnectez le compte).")
        return bid

    def _media_url(self, publication) -> str:
        mids = publication.media_ids or []
        if not mids:
            raise RuntimeError("Pinterest : un Pin requiert une image (pas de post texte seul).")
        mid = mids[0]
        sig = media_signature(mid)
        return f"{settings.public_base_url.rstrip('/')}/m/{mid}?sig={sig}"

    def publish(self, publication, account=None) -> tuple[str, str]:
        token = self._token(account)
        if publication.remote_post_id:
            return publication.remote_post_id, publication.remote_url or ""

        board_id = self._board_id(account)
        caption = (publication.caption or "").strip()
        if not caption:
            raise RuntimeError("Pinterest : texte vide.")
        body = {
            "board_id": board_id,
            "title": caption[:MAX_TITLE],
            "description": caption[:MAX_DESC],
            "media_source": {"source_type": "image_url", "url": self._media_url(publication)},
        }
        r = httpx.post(
            f"{API}/pins",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=60.0,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Pinterest publish {r.status_code}: {r.text[:200]}")
        data = r.json()
        pin_id = data.get("id", "")
        url = (
            (data.get("media") or {}).get("images", {}).get("orig", {}).get("url", "")
            or f"https://www.pinterest.com/pin/{pin_id}/"
        )
        return str(pin_id), url

    def delete(self, publication, account=None) -> None:
        token = self._token(account)
        pin_id = publication.remote_post_id
        if not pin_id:
            return
        r = httpx.delete(f"{API}/pins/{pin_id}", headers={"Authorization": f"Bearer {token}"}, timeout=30.0)
        if r.status_code >= 400 and r.status_code != 404:
            raise RuntimeError(f"Pinterest delete {r.status_code}: {r.text[:200]}")

    def insights(self, publication, account=None) -> dict:
        """Pin lifetime analytics mapped to canonical keys (best-effort)."""
        token = self._token(account)
        pin_id = publication.remote_post_id
        if not pin_id:
            return {}
        r = httpx.get(
            f"{API}/pins/{pin_id}/analytics",
            params={"start_date": "2020-01-01", "end_date": "2030-01-01", "metric_types": "IMPRESSION,SAVE,PIN_CLICK"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        if r.status_code >= 400:
            logger.warning("pinterest insights %s: %s", r.status_code, r.text[:200])
            return {}
        out: dict = {}
        for m in (r.json().get("lifetime_metrics") or []):
            name = m.get("metric_type")
            value = m.get("value")
            if name == "IMPRESSION":
                out["impressions"] = value
            elif name == "SAVE":
                out["saves"] = value
            elif name == "PIN_CLICK":
                out["views"] = value
        return out

    def comments(self, publication, account=None) -> list[dict]:
        """Pinterest comment APIs aren't in the standard scopes — return empty."""
        return []

    def reply(self, comment, account=None) -> str:
        """Pinterest comment APIs aren't in the standard scopes — refuse."""
        raise RuntimeError("Réponse Pinterest non disponible (API commentaires non exposée).")
