"""Platform adapters — one interface for every network (real where available, mock elsewhere)."""
from app.platforms.bluesky import BlueskyAdapter
from app.platforms.facebook import FacebookAdapter
from app.platforms.instagram import InstagramAdapter
from app.platforms.linkedin import LinkedInAdapter
from app.platforms.mock import MockAdapter
from app.platforms.pinterest import PinterestAdapter
from app.platforms.threads import ThreadsAdapter
from app.platforms.tiktok import TikTokAdapter
from app.platforms.x import XAdapter

_ADAPTERS = {
    "tiktok": TikTokAdapter(),
    "instagram": InstagramAdapter(),
    "facebook": FacebookAdapter(),
    "linkedin": LinkedInAdapter(),
    "x": XAdapter(),
    "youtube": MockAdapter(),
    "threads": ThreadsAdapter(),
    "pinterest": PinterestAdapter(),
    "google": MockAdapter(),
    "bluesky": BlueskyAdapter(),
}


def get_adapter(platform: str):
    """Return the adapter for a platform (real driver if present, else mock).

    En mode démo, toutes les plateformes passent par l'adaptateur factice : le produit se visite
    alors sans aucune app développeur et sans un seul compte réseau réel, ce qui est exactement ce
    qu'une démonstration doit permettre.
    """
    from app.llm import is_demo_mode

    if is_demo_mode():
        return MockAdapter()
    return _ADAPTERS.get(platform, MockAdapter())
