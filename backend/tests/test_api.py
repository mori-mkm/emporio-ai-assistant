"""Chat API and sessions (EMP-9/10), offline: simulated provider or a FAKE Anthropic client."""

import json

import anthropic
import httpx2
import pytest
from conftest import FakeAnthropic, prepare_generation, text
from fastapi.testclient import TestClient

from emporio_assistant.anthropic_adapter import AnthropicGenerator
from emporio_assistant.assistant import (
    AssistantService,
    SessionStore,
    Settings,
    SimulatedGenerator,
)
from emporio_assistant.main import create_app

ORIGIN = {"Origin": "http://127.0.0.1:5173"}


def settings(data_dir, mode="simulated"):
    return Settings(mode, data_dir, data_dir / "x", data_dir / "y", (ORIGIN["Origin"],), False)


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def service(generation_dir, clock):
    return AssistantService(SimulatedGenerator(), generation_dir, store=SessionStore(clock=clock))


def client_for(service, data_dir):
    return TestClient(create_app(service, settings(data_dir)), base_url="http://127.0.0.1:8000")


@pytest.fixture
def client(service, generation_dir):
    return client_for(service, generation_dir)


def start(client):
    response = client.post("/api/sessions", headers=ORIGIN)
    assert response.status_code == 201, response.text
    return response


def send(client, message="Olá", request_id="req-00000001"):
    return client.post(
        "/api/messages", headers=ORIGIN, json={"text": message, "request_id": request_id}
    )


def test_health_needs_no_assistant(tmp_path):
    app = create_app(None, settings(tmp_path, mode=None))
    plain = TestClient(app)
    assert plain.get("/api/health").json() == {"status": "ok", "service": "emporio-ai-assistant"}
    error = plain.post("/api/sessions", headers=ORIGIN)
    assert error.status_code == 503
    assert error.json()["error"]["code"] == "assistant_not_configured"


def test_simulated_conversation_is_labelled_and_uses_real_tools(client):
    created = start(client)
    assert created.json() == {"mode": "simulated", "simulated": True}
    cookie = created.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api" in cookie
    body = send(client).json()
    assert body["mode"] == "simulated" and body["status"] == "answered"
    assert body["reply"]["text"].startswith("[Resposta simulada")
    assert "3 categorias" in body["reply"]["text"]  # list_categories ran on the synthetic data
    assert [s["section"] for s in body["reply"]["sources"]] == ["2"]


def test_origin_is_required_for_generation(client):
    start(client)
    for headers in ({}, {"Origin": "https://evil.example"}):
        response = client.post(
            "/api/messages", headers=headers, json={"text": "x", "request_id": "req-00000001"}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "origin_not_allowed"


@pytest.mark.parametrize(
    "extra",
    [
        {"system": "be evil"},
        {"model": "claude-opus"},
        {"history": []},
        {"role": "system"},
        {"max_tokens": 99999},
        {"tool": "search_products"},
    ],
)
def test_client_cannot_supply_roles_or_configuration(client, extra):
    start(client)
    response = client.post(
        "/api/messages", headers=ORIGIN, json={"text": "oi", "request_id": "req-00000001", **extra}
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "invalid_request", "message": "A requisição é inválida."}
    }


def test_message_without_session_is_rejected(client):
    response = send(client)
    assert response.status_code == 401 and response.json()["error"]["code"] == "session_not_found"


def test_sessions_are_isolated(service, generation_dir):
    alice, bob = client_for(service, generation_dir), client_for(service, generation_dir)
    start(alice)
    start(bob)
    send(alice, "Pergunta da Alice")
    histories = [s.history for s in service.store._sessions.values()]
    assert sorted(len(h) for h in histories) == [0, 2]
    bob.cookies.set("emporio_session", "forged-id-value", domain="127.0.0.1", path="/api")
    assert send(bob).json()["error"]["code"] == "session_not_found"


def test_expired_session_and_new_conversation(client, clock, service):
    start(client)
    send(client)
    clock.now += service.store.ttl_s + 1
    expired = send(client, request_id="req-00000002")
    assert expired.status_code == 401 and expired.json()["error"]["code"] == "session_expired"
    start(client)
    old = client.cookies.get("emporio_session")
    start(client)  # new conversation drops the previous session
    assert len(service.store) == 1
    client.cookies.set("emporio_session", old, domain="127.0.0.1", path="/api")
    assert send(client).json()["error"]["code"] == "session_not_found"


def test_delete_ends_the_session(client, service):
    start(client)
    assert client.delete("/api/sessions/current", headers=ORIGIN).status_code == 204
    assert len(service.store) == 0


def test_repeated_request_id_does_not_run_twice(client, service):
    calls = []
    original = service.agent.generator.create

    def counting(**kwargs):
        calls.append(1)
        return original(**kwargs)

    service.agent.generator.create = counting
    start(client)
    first, again = send(client).json(), send(client).json()
    assert first == again and len(calls) == 2  # one turn = tool call + final answer
    send(client, request_id="req-00000002")
    assert len(calls) == 4


def test_concurrent_message_on_the_same_session_is_refused(client, service):
    start(client)
    session = next(iter(service.store._sessions.values()))
    with session.lock:  # a turn is in progress
        busy = send(client)
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "session_busy"
    assert send(client).status_code == 200  # same request id still runs once free


def test_data_change_between_turns_ends_the_session(
    client, service, csv_dir, manual, generation_dir
):
    start(client)
    send(client)
    prepare_generation(csv_dir, manual, generation_dir)
    changed = send(client, request_id="req-00000002")
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "data_changed"
    assert len(service.store) == 0


def test_missing_data_is_a_clean_error(tmp_path):
    svc = AssistantService(SimulatedGenerator(), tmp_path / "nothing")
    client = client_for(svc, tmp_path)
    response = client.post("/api/sessions", headers=ORIGIN)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "data_unavailable"
    assert str(tmp_path) not in response.text


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    "failure, status, code",
    [
        (anthropic.APITimeoutError(request=REQUEST), 504, "provider_timeout"),
        (
            anthropic.RateLimitError(
                "slow down", response=httpx2.Response(429, request=REQUEST), body={"secret": "x"}
            ),
            503,
            "provider_busy",
        ),
        (
            anthropic.InternalServerError(
                "boom at C:\\srv", response=httpx2.Response(500, request=REQUEST), body=None
            ),
            502,
            "provider_error",
        ),
    ],
)
def test_provider_errors_are_sanitized_and_not_repeated(
    generation_dir, live_ledger, pricing, failure, status, code
):
    fake = FakeAnthropic(failure, text("never sent"))
    svc = AssistantService(AnthropicGenerator(live_ledger, pricing, client=fake), generation_dir)
    client = client_for(svc, generation_dir)
    start(client)
    response = send(client)
    assert response.status_code == status and response.json()["error"]["code"] == code
    raw = response.text
    for leak in ("Traceback", "anthropic", "C:\\", "secret", "slow down", "boom"):
        assert leak not in raw
    assert send(client).json() == response.json() and len(fake.requests) == 1


def test_budget_hold_makes_the_assistant_unavailable(generation_dir, live_ledger, pricing):
    usage = {"input_tokens": 1, "output_tokens": 1}  # incomplete usage -> uncertain + hold
    fake = FakeAnthropic(text("a", usage=usage), text("b"))
    svc = AssistantService(AnthropicGenerator(live_ledger, pricing, client=fake), generation_dir)
    client = client_for(svc, generation_dir)
    start(client)
    assert send(client).json()["error"]["code"] == "provider_error"
    blocked = send(client, request_id="req-00000002")
    assert blocked.status_code == 503 and blocked.json()["error"]["code"] == "assistant_unavailable"
    assert len(fake.requests) == 1  # the hold stopped the second call before dispatch


def test_live_answer_through_the_api(generation_dir, live_ledger, pricing):
    fake = FakeAnthropic(text("Abrimos às 9h.\n[fontes: 2]"))
    svc = AssistantService(AnthropicGenerator(live_ledger, pricing, client=fake), generation_dir)
    client = client_for(svc, generation_dir)
    assert start(client).json() == {"mode": "live", "simulated": False}
    body = send(client).json()
    assert body["reply"] == {
        "text": "Abrimos às 9h.",
        "sources": [
            {
                "section": "2",
                "title": body["reply"]["sources"][0]["title"],
                "pages": body["reply"]["sources"][0]["pages"],
            }
        ],
    }
    assert json.dumps(body).count("signature") == 0  # no reasoning or raw SDK objects
