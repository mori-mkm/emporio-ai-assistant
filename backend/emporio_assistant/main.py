"""FastAPI application entry point.

No CORS middleware: in development the Vite server proxies `/api` to this
process, so the browser only talks to one origin. Cross-origin requests from
any other site get no CORS headers and are blocked by the browser.
"""

from fastapi import FastAPI

app = FastAPI(title="Empório AI Assistant API", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness only: touches no provider, database, file or credential."""
    return {"status": "ok", "service": "emporio-ai-assistant"}
