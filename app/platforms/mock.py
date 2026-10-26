"""Mock platform adapter — full pipeline demoable without live credentials.

Also produces deterministic, time-growing post insights so the Performance tab
shows realistic trends with zero real API calls.
"""
import calendar
import hashlib
import time
import uuid

# Namespace for deriving stable, idempotent remote ids from a publication's
# idempotency key (uuid5 is deterministic: same key → same id).
_ID_NAMESPACE = uuid.NAMESPACE_URL


class MockAdapter:
    """Simulates a social platform's publish + insights endpoints.

    `publish` returns a deterministic fake remote id + a mock URL. Idempotency:
    the remote id is derived from the publication's idempotency_key, so a retry
    returns the SAME remote id (no double post).

    `insights` returns fake post metrics seeded by the publication key and growing
    with elapsed time since publication — enough for a believable dashboard.
    """

    name = "mock"

    def publish(self, publication, account=None) -> tuple[str, str]:
        key = publication.idempotency_key or uuid.uuid4().hex
        remote_id = uuid.uuid5(_ID_NAMESPACE, f"mock:{publication.platform}:{key}").hex[:16]
        url = f"https://mock.{publication.platform}.com/@votre_compte/post/{remote_id}"
        return remote_id, url

    def insights(self, publication, account=None) -> dict:
        key = publication.idempotency_key or publication.remote_post_id or "seed"
        seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)

        elapsed_h = 0.0
        if publication.published_at is not None:
            try:
                epoch = calendar.timegm(publication.published_at.timetuple())
                elapsed_h = max(0.0, (time.time() - epoch) / 3600.0)
            except Exception:
                elapsed_h = 0.0

        base = 50 + seed % 400  # deterministic per-post baseline
        r = lambda x: max(0, int(x))  # noqa: E731
        metrics = {
            "impressions": r(base * 6 + elapsed_h * (30 + seed % 40)),
            "reach": r(base * 4 + elapsed_h * (22 + seed % 30)),
            "views": r(base * 5 + elapsed_h * (26 + seed % 35)),
            "likes": r(base * 0.4 + elapsed_h * (2 + seed % 4)),
            "comments": r(base * 0.05 + elapsed_h * (0.3 + (seed % 10) / 10.0)),
            "shares": r(base * 0.03 + elapsed_h * (0.2 + (seed % 8) / 10.0)),
        }
        if publication.platform == "instagram":
            metrics["saves"] = r(base * 0.04 + elapsed_h * (0.3 + (seed % 6) / 10.0))
        return metrics

    def comments(self, publication, account=None) -> list[dict]:
        """Return a fixed, deterministic set of comments covering green/orange/red."""
        key = publication.idempotency_key or publication.remote_post_id or "seed"
        base = [
            ("Alice", "Superbe publication, merci pour le partage !"),
            ("Bob", "C'est combien ?"),
            ("Claire", "J'adore, continuez comme ça 🔥"),
            ("David", "Comment on fait pour commander ?"),
            ("Emma", "Remboursez-moi, c'est une arnaque, je vais porter plainte !"),
            ("Franck", "Le colis est arrivé cassé."),
            ("Gina", "Mouais, pas convaincu."),
        ]
        out = []
        for i, (name, text) in enumerate(base):
            rid = uuid.uuid5(uuid.NAMESPACE_URL, f"{key}:comment:{i}").hex[:16]
            out.append({"remote_comment_id": rid, "author_name": name, "text": text})
        return out

    def reply(self, comment, account=None) -> str:
        """Simulate posting a reply (no network) — deterministic fake id."""
        rid = uuid.uuid5(uuid.NAMESPACE_URL, f"{comment.remote_comment_id}:reply").hex[:16]
        return rid

    def delete(self, publication, account=None) -> None:
        """No-op: mock posts live only in the local DB."""
        return None
