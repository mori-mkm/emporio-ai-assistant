"""FastAPI application entry point.

No CORS middleware: in development the Vite server proxies `/api` to this
process, so the UI and API share one origin. Without CORS headers, browsers
won't let scripts on other sites read responses, but requests can still reach
the server. CORS is not authentication or authorization.

Chat endpoints use an opaque HttpOnly SameSite=Strict session cookie; operations that can
start a model call also require an allowed `Origin` header. Endpoints are synchronous, so
FastAPI runs them in its thread pool and a slow provider call never blocks the event loop.
Run a single worker: sessions live in this process's memory.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .assistant import AssistantService, PublicError, Settings, build_service, not_configured
from .generation import AdapterConfigError

log = logging.getLogger(__name__)
COOKIE = "emporio_session"


class MessageIn(BaseModel):
    """The client sends text and a request id only: no roles, history, model or limits."""

    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=2000)
    request_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")


def create_app(
    service: AssistantService | None = None, settings: Settings | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    if service is None:
        try:
            service = build_service(settings)
        except AdapterConfigError as error:
            log.error("assistant disabled: %s", error)  # local diagnostics only
            service = None
    app = FastAPI(title="Empório AI Assistant API", version="0.2.0")
    app.state.service = service

    @app.exception_handler(PublicError)
    def public_error(_request: Request, error: PublicError) -> JSONResponse:
        return JSONResponse(error.body(), status_code=error.status)

    @app.exception_handler(RequestValidationError)
    def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
        # FastAPI's default echoes the input back; this response never does.
        body = {"error": {"code": "invalid_request", "message": "A requisição é inválida."}}
        return JSONResponse(body, status_code=422)

    @app.exception_handler(Exception)
    def internal_error(_request: Request, _error: Exception) -> JSONResponse:
        log.exception("unhandled error")
        body = {"error": {"code": "internal_error", "message": "Erro interno. Tente novamente."}}
        return JSONResponse(body, status_code=500)

    def require_origin(request: Request) -> None:
        if request.headers.get("origin") not in settings.allowed_origins:
            raise PublicError("origin_not_allowed", 403, "Origem da requisição não permitida.")

    def current_service() -> AssistantService:
        if app.state.service is None:
            raise not_configured()
        return app.state.service

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness only: touches no provider, database, file or credential."""
        return {"status": "ok", "service": "emporio-ai-assistant"}

    @app.post("/api/sessions", status_code=201)
    def new_session(request: Request, response: Response) -> dict:
        """Start a new conversation (also discards the caller's previous one)."""
        require_origin(request)
        assistant = current_service()
        session = assistant.start_session(request.cookies.get(COOKIE))
        response.set_cookie(
            COOKIE,
            session.id,
            httponly=True,
            samesite="strict",
            secure=settings.cookie_secure,
            path="/api",
            max_age=int(assistant.store.ttl_s),
        )
        return {"mode": assistant.mode, "simulated": assistant.mode == "simulated"}

    @app.post("/api/messages")
    def send_message(message: MessageIn, request: Request) -> dict:
        require_origin(request)
        assistant = current_service()
        return assistant.send(request.cookies.get(COOKIE), message.text, message.request_id)

    @app.delete("/api/sessions/current", status_code=204)
    def end_session(request: Request) -> Response:
        require_origin(request)
        current_service().end_session(request.cookies.get(COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/api")
        return response

    return app


app = create_app()
