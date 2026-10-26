"""One place that decides whether this server may reach a host, and how it connects.

Two surfaces feed this module, and neither address is chosen by our code:

  * the URL a **provider returns** in its response body (the image path fetches it);
  * the **base_url** an operator stores, or types into the settings routes — the connectivity check, the
    model listing and the two generation calls all post to it. `require_admin` on a whitelabel instance
    is the client's own admin, so it is a stranger's value.

The rules, in one place:

  * **https only** — a scheme we did not intend is a request we cannot reason about;
  * every address the host resolves to must be **globally routable** (`is_global`): loopback, private,
    link-local, unique-local, reserved and multicast are refused, and a name that resolves to several
    addresses is refused if ANY of them is not routable — a second A record is the usual way past a check
    that reads only the first;
  * the connection is **pinned** to the address that was approved, with the hostname kept for SNI,
    certificate verification and the Host header. Asking about a name and then handing the name to httpx
    leaves a gap: httpx resolves again at connect time, and a short TTL or an alternating authoritative
    answer would pass the check and connect somewhere else. Two reviews here were about exactly that gap
    — first that the fetch was unguarded, then that it was checked but not pinned, on the paths that
    carry the client's own content;
  * **no redirects** are followed (a 3xx moves the decision to the far end), and the body is **capped**,
    including the `data:` URL and `b64_json` shapes that arrive inside a response rather than over a
    socket;
  * a malformed URL leaves through `MediaFetchRefused` like any other refusal — `urlsplit` raises
    ValueError for a bad port or IPv6 brackets, and a caller that catches only this module's exception
    would otherwise turn a refusal into a 500.

One deliberate exception, and it is narrow: `allow_loopback`, for a deployment running a local provider.
The rule is `app/llm/__init__.py:local_endpoint_allowed` — the address about to be fetched must be the
endpoint we ship for that provider or one this deployment declared in its environment (`SP_LOCAL_LLM_URL`,
a file the operator writes). A **stored** setting grants nothing: it is written through the settings API by
the same admin this guard constrains, and two requests (save a loopback URL, then ask for a model list) would
otherwise put every loopback port of the host within reach. Four versions of this sentence described
something the code then did not do — a review read each one against the code — so it now names the predicate
rather than restating it. Only loopback passes, by literal or by a name that resolves to nothing else; a
private LAN address, where routers and other machines live, is refused and would need its own declared
exemption. What the allowance actually keys off is the provider **mode** and the address: for `ollama`, the two
addresses that mode implies — the endpoint we ship and the one the deployment declared — are reachable,
whoever named them. On the settings routes the mode comes from the request; on the generation paths it comes
from the stored row, which is the deployment's own choice. There is no other switch, and this paragraph is the
place that says so: the trade is deliberate (the alternative refused the operator the use of their own local
provider through the app's own settings screen), and it is bounded to one known endpoint of a service the
instance talks to by design whenever that mode is offered.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse

import httpx

MAX_MEDIA_BYTES = 25 * 1024 * 1024
TIMEOUT_S = 120.0


class MediaFetchRefused(RuntimeError):
    """The address was not something we will reach. Never retried — refused on purpose."""


def _split(url: str):
    """urlsplit that refuses through this module's own exception (see the module docstring)."""
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        parsed.hostname, parsed.port          # touch the properties that can raise
    except ValueError as exc:
        raise MediaFetchRefused(f"the URL is malformed: {exc}") from exc
    return parsed


def _addresses(host: str, port: int) -> list[str]:
    """The addresses `host` resolves to — refusing if ANY of them is not globally routable."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise MediaFetchRefused(f"the URL is a non-routable address ({literal})")
        return [str(literal)]
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise MediaFetchRefused(f"{host} does not resolve ({exc})") from exc
    if not infos:
        raise MediaFetchRefused(f"{host} resolves to nothing")
    addresses: list[str] = []
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            raise MediaFetchRefused(
                f"{host} resolves to a non-routable address ({addr}) — refusing the whole name")
        if str(addr) not in addresses:
            addresses.append(str(addr))
    return addresses


def _loopback_addresses(host: str, port: int) -> list[str]:
    """The loopback addresses `host` resolves to; raises if it resolves to anything else too."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_loopback:
            raise MediaFetchRefused(f"{host} is not a loopback address")
        return [str(literal)]
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise MediaFetchRefused(f"{host} does not resolve ({exc})") from exc
    addresses = []
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_loopback:
            raise MediaFetchRefused(f"{host} resolves to {addr}, which is not loopback")
        if str(addr) not in addresses:
            addresses.append(str(addr))
    if not addresses:
        raise MediaFetchRefused(f"{host} resolves to nothing")
    return addresses


def _loopback_only(host: str, port: int) -> bool:
    """True when every address `host` resolves to is loopback, and nothing else.

    A literal loopback address passes. A *name* has to resolve, and every answer has to be loopback — so
    `localhost` does. That name matters: it is the shipped Ollama default, and the first version of this
    check accepted literals only, which quietly broke a local provider that had been working. A name that
    answers both 127.0.0.1 and a public address does not pass.
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return literal.is_loopback
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    if not infos:
        return False
    return all(ipaddress.ip_address(info[4][0]).is_loopback for info in infos)


def assert_fetchable(url: str, *, allow_loopback: bool = False) -> None:
    """Raise MediaFetchRefused unless the URL is a public https one (or a declared local provider)."""
    if not isinstance(url, str) or not url.strip():
        raise MediaFetchRefused("the provider returned no usable URL")
    parsed = _split(url)
    host = (parsed.hostname or "").strip()
    if not host:
        raise MediaFetchRefused("the URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        _addresses(host, port)
        return
    if allow_loopback and parsed.scheme == "http" and _loopback_only(host, port):
        return
    raise MediaFetchRefused(
        f"refusing {parsed.scheme or 'a URL with no'}:// — https only, unless this is a saved local "
        f"provider on loopback")


def _pinned(url: str, *, allow_loopback: bool = False) -> tuple[str, dict[str, str], str]:
    """(URL to connect to, Host header, hostname) for a URL that has just been checked.

    Everything here reaches a provider through this function, so a name is resolved once, checked, and then
    *used* — never handed to httpx to resolve a second time. The hostname comes back separately because the
    request must still present it: for SNI and certificate verification on https.
    """
    assert_fetchable(url, allow_loopback=allow_loopback)
    parsed = _split(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        address, default_port = _addresses(host, port)[0], 443
    else:
        # A saved local provider. This branch resolves a *name* now that the allowance covers names
        # (`localhost`), so it has to pin like the other one — otherwise the name goes to httpx and is
        # resolved a second time, which is the gap this whole module exists to close.
        address, default_port = _loopback_addresses(host, port)[0], 80
    netloc = f"[{address}]" if ":" in address else address
    if port != default_port:
        netloc = f"{netloc}:{port}"
    headers = {"Host": host if port == default_port else f"{host}:{port}"}
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, "")), headers, host


def guarded_request(method: str, url: str, *, headers: dict | None = None, json_body=None,
                    timeout: float = 20.0, allow_loopback: bool = False) -> httpx.Response:
    """One provider request, pinned to the address the guard approved."""
    target, host_headers, host = _pinned(url, allow_loopback=allow_loopback)
    sent = dict(headers or {})
    sent.update(host_headers)
    https = urllib.parse.urlsplit(target).scheme == "https"
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        request = client.build_request(method, target, headers=sent, json=json_body,
                                       extensions={"sni_hostname": host} if https else {})
        response = client.send(request)
    if 300 <= response.status_code < 400:
        # Refused rather than followed — and refused rather than *reported as connected*: the caller would
        # otherwise answer "Connecté (301)" while the request never reached what it asked for, and the
        # same configuration would fail on the model listing. One address, one answer.
        raise MediaFetchRefused(
            f"the address redirected ({response.status_code} to "
            f"{response.headers.get('location', '?')[:80]}) — a redirect moves the decision to the far "
            f"end, so it is refused rather than followed")
    return response


def fetch_media(url: str, *, max_bytes: int = MAX_MEDIA_BYTES, timeout: float = TIMEOUT_S) -> bytes:
    """Bytes of an image the provider pointed at, or a refusal. Streams, so the cap holds mid-download."""
    target, host_headers, host = _pinned(url)
    total = 0
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            request = client.build_request("GET", target, headers=host_headers,
                                           extensions={"sni_hostname": host})
            resp = client.send(request, stream=True)
            try:
                if 300 <= resp.status_code < 400:
                    raise MediaFetchRefused(
                        f"the URL redirected ({resp.status_code}) — a redirect moves the decision to "
                        f"the far end, so it is refused rather than followed")
                resp.raise_for_status()
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise MediaFetchRefused(f"the media is {declared} bytes, over the {max_bytes} cap")
                chunks: list[bytes] = []
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaFetchRefused(
                            f"the media exceeded the {max_bytes} byte cap mid-download")
                    chunks.append(chunk)
            finally:
                resp.close()
    except httpx.HTTPError as exc:
        raise MediaFetchRefused(f"the media could not be fetched: {exc}") from exc
    if not total:
        raise MediaFetchRefused("the URL returned an empty body")
    return b"".join(chunks)
