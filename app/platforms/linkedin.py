"""LinkedIn adapter — real publish via the "Share on LinkedIn" API (api.linkedin.com).

Posts go to `POST /rest/posts` with `author` = the member URN
(urn:li:person:<sub>) resolved at connect time via OpenID `/v2/userinfo`. Text-only
for now; image/video posts need the assets upload flow, and company-page posting +
comments/insights require the Community Management API (LinkedIn Marketing
Developer Platform) — both are follow-ups, so those methods are honest stubs.

Auth is OAuth 2.0 (authorization code) against www.linkedin.com; the access token
is stored encrypted and the author URN in account_meta.
"""
import logging
from urllib.parse import quote

import httpx

from app.security import decrypt_secret

logger = logging.getLogger("social_pilot.linkedin")

API = "https://api.linkedin.com"
POSTS = f"{API}/rest/posts"
VERSION = "202606"  # LinkedIn-Version header (YYYYMM); versions sunset ~1yr, so bump as needed
MAX_TEXT = 3000  # LinkedIn's hard limit on a text post


class LinkedInAdapter:
    name = "linkedin"

    def _token(self, account):
        if account is None or not account.token_enc:
            raise RuntimeError("Compte LinkedIn non connecté (OAuth requis).")
        return decrypt_secret(account.token_enc)

    def _author(self, account) -> str:
        urn = (account.account_meta or {}).get("author_urn")
        if not urn:
            raise RuntimeError("LinkedIn : author URN manquant (reconnectez le compte).")
        return urn

    def _headers(self, token):
        return {
            "Authorization": f"Bearer {token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": VERSION,
            "Content-Type": "application/json",
        }

    def publish(self, publication, account=None) -> tuple[str, str]:
        token = self._token(account)
        if publication.remote_post_id:
            return publication.remote_post_id, publication.remote_url or ""

        author = self._author(account)
        caption = (publication.caption or "").strip()
        if not caption:
            raise RuntimeError("LinkedIn : texte vide.")

        # Text-only post. Image/video need the assets upload flow — not yet wired.
        body = {
            "author": author,
            "commentary": caption[:MAX_TEXT],
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        r = httpx.post(POSTS, headers=self._headers(token), json=body, timeout=60.0)
        if r.status_code >= 400:
            raise RuntimeError(f"LinkedIn publish {r.status_code}: {r.text[:200]}")

        # The new post URN arrives as the x-restli-id header (or `id` in the body).
        post_urn = r.headers.get("x-restli-id") or ""
        if not post_urn:
            try:
                post_urn = (r.json() or {}).get("id", "")
            except Exception:  # noqa: BLE001 — non-JSON body
                post_urn = ""
        url = f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else ""
        return post_urn, url

    # -- gated features (Community Management API / Marketing Developer Platform) --
    def insights(self, publication, account=None) -> dict:
        """Not available via the Share-on-LinkedIn product — needs the Marketing
        Developer Platform. Honest stub: return nothing rather than fabricate."""
        return {}

    def comments(self, publication, account=None) -> list[dict]:
        """Same gating as insights — return empty."""
        return []

    def reply(self, comment, account=None) -> str:
        """Same gating as insights — refuse rather than mark replied locally."""
        raise RuntimeError("Réponse LinkedIn non disponible (API Community Management requise).")

    def delete(self, publication, account=None) -> None:
        """Delete a published post (w_member_social). Idempotent: 204 on success,
        404 is treated as already-gone."""
        token = self._token(account)
        urn = publication.remote_post_id
        if not urn:
            return
        r = httpx.delete(
            f"{POSTS}/{quote(urn, safe='')}",
            headers={**self._headers(token), "X-RestLi-Method": "DELETE"},
            timeout=30.0,
        )
        if r.status_code >= 400 and r.status_code != 404:
            raise RuntimeError(f"LinkedIn delete {r.status_code}: {r.text[:200]}")
