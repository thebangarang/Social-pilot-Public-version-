"""The media-fetch guard's cases — the answer to a second review's SSRF finding.

The fetch in `app/llm/__init__.py` takes its URL from the **provider's response body**, and the provider's
base URL is an admin setting. So the address the server connects to can be chosen by whatever answers at
that base URL: a metadata service, a database admin port, the gateway itself. These cases are written
against `app/net_guard.py`, which is now the only thing that decides.

Nothing here touches the network: every case is a URL the guard must refuse before any connection, plus
the two shapes it must accept (a data URL never reaches it — `app/llm` handles that first).
"""
from __future__ import annotations

import unittest

from app.net_guard import MediaFetchRefused, assert_fetchable


class Refuses(unittest.TestCase):
    def refuse(self, url: str) -> None:
        with self.assertRaises(MediaFetchRefused, msg=f"{url} should have been refused"):
            assert_fetchable(url)

    def test_cloud_metadata_is_refused(self):
        # The canonical SSRF target: link-local, and the address that makes this finding matter.
        self.refuse("http://169.254.169.254/latest/meta-data/iam/security-credentials/")
        self.refuse("https://169.254.169.254/latest/meta-data/")
        self.refuse("https://[fe80::1]/")

    def test_loopback_and_private_ranges_are_refused(self):
        for url in ("https://127.0.0.1/admin", "https://127.0.0.1:5432/",
                    "https://10.0.0.5/", "https://172.16.4.4/", "https://192.168.50.1/",
                    "https://[::1]/", "https://[fd00::1]/", "https://0.0.0.0/"):
            with self.subTest(url=url):
                self.refuse(url)

    def test_a_name_resolving_to_a_private_address_is_refused(self):
        # "localhost" resolves to a loopback address without being a literal — the check must resolve.
        self.refuse("https://localhost/img.png")

    def test_a_scheme_other_than_https_is_refused(self):
        # http:// to a public host is still refused: HTTPS-only is a rule, not a preference, and file://
        # or gopher:// are the classic redirect tricks.
        for url in ("http://example.com/i.png", "file:///etc/passwd", "gopher://example.com/1",
                    "ftp://example.com/i.png", "javascript:alert(1)"):
            with self.subTest(url=url):
                self.refuse(url)

    def test_empty_and_hostless_urls_are_refused(self):
        for url in ("", "   ", "https://", "https:///path", "not a url"):
            with self.subTest(url=url):
                self.refuse(url)

    def test_a_name_that_does_not_resolve_is_refused(self):
        self.refuse("https://this-name-is-not-registered-invalid.invalid/i.png")


class Accepts(unittest.TestCase):
    def test_a_public_https_url_passes_the_guard(self):
        # example.com is public and routable, so the guard lets it through to the fetch itself. This case
        # only asserts the guard's decision (no connection is attempted here).
        assert_fetchable("https://example.com/image.png")

    def test_loopback_is_only_allowed_where_it_is_declared(self):
        """`allow_loopback` is for a *saved* local provider; it must not become a general hole.

        With the flag, a loopback literal and a name that resolves to nothing but loopback
        (`localhost`, which is the shipped Ollama default — the first version of this check accepted
        literals only and so broke a local provider that had been working) pass. Loopback over https is
        still refused (the allowance is for a plain local endpoint, not a way to reach anything else),
        and private or link-local addresses are refused even with the flag: a router or a metadata
        service is not this machine.
        """
        assert_fetchable("http://127.0.0.1:11434/api/tags", allow_loopback=True)
        assert_fetchable("http://localhost:11434/api/tags", allow_loopback=True)
        with self.assertRaises(MediaFetchRefused):
            assert_fetchable("http://127.0.0.1:11434/api/tags")
        with self.assertRaises(MediaFetchRefused):
            assert_fetchable("http://localhost:11434/api/tags")
        for url in ("https://127.0.0.1/", "http://192.168.50.1/", "http://169.254.169.254/",
                    "http://10.0.0.5/"):
            with self.subTest(url=url):
                with self.assertRaises(MediaFetchRefused):
                    assert_fetchable(url, allow_loopback=True)

    def test_the_guard_is_what_the_llm_module_calls(self):
        import pathlib

        source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "llm" / "__init__.py").read_text()
        self.assertIn("from app.net_guard import fetch_media", source)
        self.assertNotIn("httpx.get(url_or_dataurl", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
