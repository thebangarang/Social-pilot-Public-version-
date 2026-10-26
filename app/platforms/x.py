"""X (Twitter) adapter — real publish via the X API v2 (pay-per-use).

Publish is POST /2/tweets (text-only; image/video media upload is a follow-up —
the v2 chunked upload flow is separate). Delete is DELETE /2/tweets/{id}.
Insights map public_metrics to canonical keys.

Comments/reply are honest stubs for now: X restricts programmatic replies to
"summoned" replies (the author must @mention you), and reading a conversation's
replies needs paid search access — neither fits the community inbox yet.

Auth is OAuth 2.0 Authorization Code + PKCE (S256). The account stores the
access token; the refresh token (offline.access scope) lives in
refresh_token_enc. Note: X post length is 280 chars for free accounts, up to
25k for Premium — the tier is stored at connect time in a follow-up; v1
enforces the 280 free default.
"""
import logging

import httpx

from app.security import decrypt_secret

logger = logging.getLogger("social_pilot.x")

API = "https://api.x.com/2"
POST_URL_TMPL = "https://x.com/i/web/status/{id}"
MAX_TEXT = 280  # free-account limit; Premium is higher (future: store at connect)


class XAdapter:
    name = "x"

    def _token(self, account):
        if account is None or not account.token_enc:
            raise RuntimeError("Compte X non connecté (OAuth requis).")
        return decrypt_secret(account.token_enc)

    def publish(self, publication, account=None) -> tuple[str, str]:
        token = self._token(account)
        if publication.remote_post_id:
            return publication.remote_post_id, publication.remote_url or ""

        caption = (publication.caption or "").strip()
        if not caption:
            raise RuntimeError("X : texte vide.")
        if len(caption) > MAX_TEXT:
            raise RuntimeError(f"X : texte de plus de {MAX_TEXT} caractères.")
        if publication.media_ids:
            raise RuntimeError("X : publication d'image non prise en charge (texte seul pour l'instant).")

        r = httpx.post(
            f"{API}/tweets",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": caption},
            timeout=30.0,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"X publish {r.status_code}: {r.text[:200]}")
        tid = str((r.json().get("data") or {}).get("id", ""))
        return tid, POST_URL_TMPL.format(id=tid)

    def delete(self, publication, account=None) -> None:
        token = self._token(account)
        tid = publication.remote_post_id
        if not tid:
            return
        r = httpx.delete(f"{API}/tweets/{tid}", headers={"Authorization": f"Bearer {token}"}, timeout=30.0)
        if r.status_code >= 400 and r.status_code != 404:
            raise RuntimeError(f"X delete {r.status_code}: {r.text[:200]}")

    def insights(self, publication, account=None) -> dict:
        token = self._token(account)
        tid = publication.remote_post_id
        if not tid:
            return {}
        r = httpx.get(
            f"{API}/tweets/{tid}",
            params={"tweet.fields": "public_metrics"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        if r.status_code >= 400:
            logger.warning("x insights %s: %s", r.status_code, r.text[:200])
            return {}
        pm = (r.json().get("data") or {}).get("public_metrics") or {}
        out: dict = {}
        if "like_count" in pm:
            out["likes"] = pm["like_count"]
        if "reply_count" in pm:
            out["comments"] = pm["reply_count"]
        if "retweet_count" in pm:
            out["shares"] = pm["retweet_count"]
        if "impression_count" in pm:
            out["impressions"] = pm["impression_count"]
        # quote_count has no canonical bucket — leave it out.
        return out

    def comments(self, publication, account=None) -> list[dict]:
        """Reading a thread's replies needs paid search access — honest stub."""
        return []

    def reply(self, comment, account=None) -> str:
        raise RuntimeError("Réponse X non disponible (réponses programmées limitées aux mentions).")
