"""Session-based assistant service (EMP-9), used by the API and by the scenario runner.

Sessions live in this process's memory: opaque random ids, idle expiry, a cap on sessions and
turns, one turn at a time per session, and a per-session cache of recent request ids so a
repeated request never dispatches a second paid call. Run one worker process while sessions are
in memory (a second worker would not see them). Nothing survives a restart.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .agent import Agent, AgentConfig, TurnError
from .anthropic_adapter import AnthropicGenerator
from .budget import DEFAULT_PATH as LEDGER_PATH
from .budget import BudgetError, Ledger, UncertainUsage
from .generation import (
    END_TURN,
    TOOL_USE,
    AdapterConfigError,
    CallContext,
    ModelReply,
    PromptTooLarge,
    ProviderError,
    ToolCall,
)
from .pricing import DEFAULT_PATH as PRICING_PATH
from .pricing import PricingError, load_price_table
from .tools import DEFAULT_DATA_DIR, CatalogSnapshot, ToolError

log = logging.getLogger(__name__)
MODES = ("live", "local", "simulated")  # paid API, local inference, scripted test replies
PROVIDERS = {"live": ("anthropic", "openai"), "local": ("ollama",), "simulated": ("simulated",)}


class PublicError(Exception):
    """Stable, sanitized error for API clients. `stops_batch` tells the runner to stop."""

    def __init__(self, code: str, status: int, message: str, stops_batch: bool = False):
        super().__init__(code)
        self.code, self.status, self.message, self.stops_batch = code, status, message, stops_batch

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


def not_configured() -> PublicError:
    return PublicError(
        "assistant_not_configured",
        503,
        "O atendimento virtual não está configurado neste servidor.",
    )


def _unavailable() -> PublicError:
    return PublicError(
        "assistant_unavailable",
        503,
        "O atendimento está temporariamente indisponível. Tente novamente mais tarde.",
        stops_batch=True,
    )


TURN_ERRORS = {
    "invalid_message": (400, "A mensagem está vazia ou longa demais."),
    "conversation_limit": (409, "Esta conversa atingiu o limite. Inicie uma nova conversa."),
    "conversation_too_long": (409, "Esta conversa ficou longa demais. Inicie uma nova conversa."),
}


def _provider_error(error: Exception) -> PublicError:
    """Same public codes for every provider; SDK exception classes are matched by name so no
    provider SDK has to be imported here."""
    cause = error.__cause__ if isinstance(error, UncertainUsage) else None
    name = type(cause).__name__ if cause is not None else ""
    local = getattr(error, "code", "")
    if name in ("APITimeoutError", "TimeoutError") or local == "local_model_timeout":
        code, status, message = "provider_timeout", 504, "O serviço de IA demorou para responder."
    elif name == "RateLimitError" or local == "local_model_busy":
        code, status, message = "provider_busy", 503, "O serviço de IA está ocupado no momento."
    else:
        code, status, message = (
            "provider_error",
            502,
            "O serviço de IA não respondeu como esperado.",
        )
    return PublicError(code, status, message + " Tente novamente em instantes.", stops_batch=True)


@dataclass
class Session:
    id: str
    generation_id: str
    run_id: str
    last_used: float
    history: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    replies: OrderedDict = field(default_factory=OrderedDict)  # request_id -> reply or error
    turns: int = 0


class SessionStore:
    def __init__(self, ttl_s: float = 1800, max_sessions: int = 200, clock=time.monotonic):
        self.ttl_s, self.max_sessions, self.clock = ttl_s, max_sessions, clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, generation_id: str, run_id: str | None = None) -> Session:
        with self._lock:
            now = self.clock()
            for sid in [s.id for s in self._sessions.values() if now - s.last_used > self.ttl_s]:
                del self._sessions[sid]
            if len(self._sessions) >= self.max_sessions:
                raise PublicError(
                    "too_many_sessions", 503, "Muitas conversas ativas. Tente em instantes."
                )
            sid = secrets.token_urlsafe(32)
            session = Session(sid, generation_id, run_id or f"chat-{sid[:8]}", now)
            self._sessions[sid] = session
            return session

    def get(self, session_id: str | None) -> Session:
        with self._lock:
            session = self._sessions.get(session_id) if session_id else None
            if session is None:
                raise PublicError(
                    "session_not_found", 401, "Conversa não encontrada. Inicie uma nova conversa."
                )
            if self.clock() - session.last_used > self.ttl_s:
                del self._sessions[session.id]
                raise PublicError(
                    "session_expired", 401, "Esta conversa expirou. Inicie uma nova conversa."
                )
            session.last_used = self.clock()
            return session

    def drop(self, session_id: str | None) -> None:
        with self._lock:
            self._sessions.pop(session_id or "", None)

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        with self._lock:
            return session_id in self._sessions


class SimulatedGenerator:
    """Explicit offline provider: controlled test replies through the real loop and tools.

    It is not a model and not a rule-based assistant: every reply says it is simulated."""

    mode = "simulated"

    def describe(self) -> dict:
        return {"provider": "simulated"}

    def create(self, *, system, messages, tools, context) -> ModelReply:
        last = messages[-1]
        if last["role"] != "tool_results":
            call = ToolCall(f"sim_{secrets.token_hex(6)}", "list_categories", {})
            return ModelReply("", [call], TOOL_USE)
        outcome = json.loads(last["results"][0].content)
        count = len(outcome["data"]["categories"]) if outcome["ok"] else 0
        question = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        text = (
            "[Resposta simulada — nenhum modelo de IA foi chamado.] "
            f"Recebi: “{question[:200]}”. A ferramenta list_categories respondeu com {count} "
            "categorias nesta versão dos dados.\n[fontes: 2]"
        )
        return ModelReply(text, [], END_TURN)


class AssistantService:
    def __init__(
        self,
        generator,
        data_dir: Path = DEFAULT_DATA_DIR,
        config: AgentConfig = AgentConfig(),
        store: SessionStore | None = None,
        budget: str = "development",
        run_cap_micro_usd: int | None = None,
        policy=None,  # policy_context selector; None = full manual (baseline)
    ):
        self.mode = generator.mode
        self.agent = Agent(generator, config, policy)
        self.data_dir = data_dir
        self.store = store if store is not None else SessionStore()
        self.budget = budget
        self.run_cap_micro_usd = run_cap_micro_usd

    def start_session(self, previous_id: str | None = None, run_id: str | None = None) -> Session:
        self.store.drop(previous_id)
        with self._snapshot() as snapshot:
            return self.store.create(snapshot.id, run_id)

    def end_session(self, session_id: str | None) -> None:
        self.store.drop(session_id)

    def _snapshot(self) -> CatalogSnapshot:
        try:
            return CatalogSnapshot(self.data_dir)
        except ToolError:
            raise PublicError(
                "data_unavailable", 503, "Os dados da loja não estão disponíveis no momento."
            ) from None

    def send(
        self,
        session_id: str | None,
        text: str,
        request_id: str,
        scenario_id: str | None = None,
        diagnostics: dict | None = None,
        trace: bool = False,
        tool_call=None,
    ) -> dict:
        """Public reply only. `diagnostics` (runner use; the API never passes it) receives the
        turn status, model calls, tools, citations and ledger op ids; with `trace` also the
        ordered model/tool steps, kept there even when the turn fails. None of it enters the
        public reply. `tool_call` replaces `tools.call_tool` for one evaluation scenario
        (runner fixtures only; the API never passes it)."""
        session = self.store.get(session_id)
        if not session.lock.acquire(blocking=False):
            raise PublicError(
                "session_busy", 409, "Ainda estou respondendo a mensagem anterior. Aguarde."
            )
        try:
            if request_id in session.replies:  # repeated request: same outcome, no new call
                cached = session.replies[request_id]
                if isinstance(cached, PublicError):
                    raise cached
                return cached
            try:
                reply = self._turn(
                    session, text, request_id, scenario_id, diagnostics, trace, tool_call
                )
            except PublicError as error:
                if error.code not in ("session_busy", "data_changed"):
                    self._remember(session, request_id, error)
                raise
            self._remember(session, request_id, reply)
            return reply
        finally:
            session.lock.release()

    @staticmethod
    def _remember(session: Session, request_id: str, outcome) -> None:
        session.replies[request_id] = outcome
        while len(session.replies) > 20:
            session.replies.popitem(last=False)

    def _turn(
        self,
        session: Session,
        text: str,
        request_id: str,
        scenario_id: str | None,
        diagnostics: dict | None = None,
        trace: bool = False,
        tool_call=None,
    ) -> dict:
        steps = None
        extra = {"tool_call": tool_call} if tool_call is not None else {}
        if trace and diagnostics is not None:
            steps = diagnostics["trace"] = []
        with self._snapshot() as snapshot:
            if snapshot.id != session.generation_id:
                self.store.drop(session.id)
                raise PublicError(
                    "data_changed",
                    409,
                    "Os dados da loja foram atualizados. Inicie uma nova conversa.",
                )
            context = CallContext(
                run_id=session.run_id,
                scenario_id=scenario_id,
                budget=self.budget,
                run_cap_micro_usd=self.run_cap_micro_usd,
            )
            try:
                result = self.agent.run_turn(
                    snapshot, session.history, text, context, steps, **extra
                )
            except TurnError as error:
                status, message = TURN_ERRORS[error.code]
                raise PublicError(error.code, status, message) from None
            except PromptTooLarge:
                status, message = TURN_ERRORS["conversation_too_long"]
                raise PublicError("conversation_too_long", status, message) from None
            except UncertainUsage as error:
                log.warning("provider call uncertain: %s", error)
                raise _provider_error(error) from None
            except ProviderError as error:
                log.warning("local model failed: %s", error)
                raise _provider_error(error) from None
            except BudgetError as error:
                log.warning("budget refused the call: %s", error)
                raise _unavailable() from None
        if diagnostics is not None:
            if result.call_metrics:
                diagnostics["call_metrics"] = list(result.call_metrics)
            diagnostics.update(
                status=result.status,
                model_calls=result.model_calls,
                tools_used=list(result.tools_used),
                sources=list(result.sources),
                dropped_citations=result.dropped_citations,
                dropped_sections=list(result.dropped_sections),
                op_ids=list(result.op_ids),
                data_generation=snapshot.id,
                policy_context=dict(result.policy_context),
            )
        if result.status == "answered":
            session.history += [
                {"role": "user", "content": text},
                {  # lookups come from the agent's tool loop, never from model or client text
                    "role": "assistant",
                    "content": result.text,
                    "lookups": list(result.lookups),
                    "turn": session.turns + 1,
                    "data_generation": snapshot.id,
                },
            ]
        session.turns += 1
        return {
            "request_id": request_id,
            "status": result.status,
            "mode": self.mode,
            "reply": {"text": result.text, "sources": result.sources},
        }


@dataclass(frozen=True)
class Settings:
    mode: str | None
    data_dir: Path
    ledger_path: Path
    pricing_path: Path
    allowed_origins: tuple[str, ...]
    cookie_secure: bool
    provider: str | None = None  # backend configuration only; None = the default for the mode
    ollama_url: str | None = None
    prompt_cache: str = "off"  # Anthropic only: "off" or "system-5m"; never from the client

    @classmethod
    def from_env(cls) -> Settings:
        mode = os.environ.get("EMPORIO_ASSISTANT_MODE") or None
        origins = os.environ.get(
            "EMPORIO_ALLOWED_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
        )
        return cls(
            mode=mode,
            data_dir=Path(os.environ.get("EMPORIO_DATA_DIR") or DEFAULT_DATA_DIR),
            ledger_path=Path(os.environ.get("EMPORIO_LEDGER_PATH") or LEDGER_PATH),
            pricing_path=Path(os.environ.get("EMPORIO_PRICING_PATH") or PRICING_PATH),
            allowed_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            cookie_secure=os.environ.get("EMPORIO_COOKIE_SECURE", "").lower() == "true",
            provider=os.environ.get("EMPORIO_PROVIDER") or None,
            ollama_url=os.environ.get("EMPORIO_OLLAMA_URL") or None,
            prompt_cache=os.environ.get("EMPORIO_PROMPT_CACHE") or "off",
        )


def default_provider(mode: str | None) -> str:
    return {"local": "ollama", "simulated": "simulated"}.get(mode or "", "anthropic")


def build_service(settings: Settings, generator=None, **kwargs) -> AssistantService | None:
    """None when no mode is selected. Each mode needs every prerequisite; nothing falls back to
    another provider or mode. `generator` lets the runner pass a configured one."""
    if settings.mode is None:
        return None
    if settings.mode not in MODES:
        raise AdapterConfigError(f"EMPORIO_ASSISTANT_MODE must be one of {MODES}")
    provider = settings.provider or default_provider(settings.mode)
    if provider not in PROVIDERS[settings.mode]:
        raise AdapterConfigError(f"provider '{provider}' is not valid for mode '{settings.mode}'")
    if generator is None:
        generator = make_generator(settings)
    if generator.mode != settings.mode:
        raise AdapterConfigError("the generator does not match the configured mode")
    return AssistantService(generator, settings.data_dir, **kwargs)


def make_generator(settings: Settings, **overrides):
    """The configured provider's generator; `overrides` are its settings fields."""
    provider = settings.provider or default_provider(settings.mode)
    if settings.prompt_cache != "off" and provider != "anthropic":
        raise AdapterConfigError("prompt caching is implemented for the anthropic provider only")
    if provider == "simulated":
        return SimulatedGenerator()
    if provider == "ollama":
        from .ollama_adapter import OllamaGenerator, OllamaSettings

        url = {"base_url": settings.ollama_url} if settings.ollama_url else {}
        return OllamaGenerator(OllamaSettings(**url, **overrides))
    try:
        ledger, prices = Ledger(settings.ledger_path), load_price_table(settings.pricing_path)
    except (BudgetError, PricingError) as error:
        raise AdapterConfigError(f"live mode not ready: {error}") from None
    if provider == "openai":
        from .openai_adapter import OpenAIGenerator, OpenAISettings

        return OpenAIGenerator(ledger, prices, OpenAISettings(**overrides))
    from .anthropic_adapter import GenerationSettings

    cache = {"prompt_cache": settings.prompt_cache} if settings.prompt_cache != "off" else {}
    return AnthropicGenerator(ledger, prices, GenerationSettings(**cache, **overrides))
