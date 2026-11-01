"""FastAPI application entry point.

No CORS middleware: in development the Vite server proxies `/api` to this
process, so the UI and API share one origin. Without CORS headers, browsers
won't let scripts on other sites read responses, but requests can still reach
the server. CORS is not authentication or authorization.
"""

from fastapi import FastAPI

app = FastAPI(title="Empório AI Assistant API", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness only: touches no provider, database, file or credential."""
    return {"status": "ok", "service": "emporio-ai-assistant"}
