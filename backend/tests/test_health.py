from fastapi.testclient import TestClient

from emporio_assistant.main import app

client = TestClient(app)


def test_health_returns_contract():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "emporio-ai-assistant"}


def test_foreign_origin_gets_no_cors_grant():
    # The UI reaches the API through the same-origin Vite proxy; no site may read it cross-origin.
    for method, kwargs in [
        ("get", {"headers": {"Origin": "https://evil.example"}}),
        (
            "options",
            {
                "headers": {
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "GET",
                }
            },
        ),
    ]:
        response = getattr(client, method)("/api/health", **kwargs)
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers
