"""OpenAI embeddings for the experimental `embeddings` policy context (EMP-12).

Every request (index build or query) passes `budget.guarded_call`: a conservative reservation
before dispatch, SDK retries off, an explicit timeout, one ledger operation per attempt, and
the reservation kept when the outcome is unknown. Embeddings bill input tokens only, so the
usage is `{"input": prompt_tokens}`; nothing is invented for output. A response is validated
after its cost is settled: a wrong count, dimension, model or a non-finite value raises
`EmbeddingError` and no vector from it is used.

Model and dimension follow the official docs (checked 2026-11-02): text-embedding-3-small
returns 1536 values by default, `dimensions` may shorten it, vectors are normalized to length
1, the input limit is 8192 tokens, and the usage object has `prompt_tokens` / `total_tokens`.
"""

from __future__ import annotations

import math
import secrets
from array import array
from typing import Any

from .budget import CallRequest, CallResult, Ledger, guarded_call
from .generation import CallContext, ProviderError, byte_bound
from .pricing import PriceTable

PROVIDER = "openai"
MODEL = "text-embedding-3-small"
DIMENSIONS = 1536


class EmbeddingError(ProviderError):
    """The provider answered (and was paid) but the vectors cannot be used."""

    def __init__(self, detail: str):
        super().__init__("embedding_invalid", detail)


def map_usage(response: Any) -> dict | None:
    """{"input": prompt_tokens}, or None (cost unknown: the reservation stays held) when the
    usage is missing or reports tokens beyond the input."""
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None)
    total = getattr(usage, "total_tokens", None)
    if type(prompt) is not int or prompt < 0 or total != prompt:
        return None
    return {"input": prompt}


def parse_vectors(response: Any, count: int, model: str, dimensions: int) -> list[list[float]]:
    if getattr(response, "model", None) != model:
        raise EmbeddingError("the response names another model")
    data = getattr(response, "data", None)
    if not isinstance(data, list) or len(data) != count:
        raise EmbeddingError("wrong number of vectors")
    vectors: dict[int, list[float]] = {}
    for item in data:
        index, values = getattr(item, "index", None), getattr(item, "embedding", None)
        if type(index) is not int or not 0 <= index < count or index in vectors:
            raise EmbeddingError("invalid vector index")
        if not isinstance(values, list) or len(values) != dimensions:
            raise EmbeddingError("wrong vector dimension")
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            raise EmbeddingError("non-finite or non-numeric value")
        if not any(values):
            raise EmbeddingError("zero vector")
        vectors[index] = [float(v) for v in values]
    return [vectors[i] for i in range(count)]


def pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()  # float32: enough for ranking


def unpack(blob: bytes, dimensions: int) -> list[float] | None:
    values = array("f")
    values.frombytes(blob)
    if len(values) != dimensions or not all(math.isfinite(v) for v in values):
        return None
    return list(values)


def cosine(a: list[float], b: list[float]) -> float:
    """Ranking score only: similarity of wording, never factual confidence."""
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


class OpenAIEmbedder:
    provider, model, dimensions = PROVIDER, MODEL, DIMENSIONS

    def __init__(
        self,
        ledger: Ledger,
        pricing: PriceTable,
        client: Any | None = None,
        call_timeout_s: float = 20.0,
    ):
        pricing.get(self.provider, self.model)  # unknown price: refuse before any client
        self.ledger, self.pricing, self.call_timeout_s = ledger, pricing, call_timeout_s
        if client is None:
            from .openai_adapter import _new_client  # key from the process env, retries off

            client = _new_client(call_timeout_s)
        self.client = client

    def embed(self, texts: list[str], context: CallContext) -> tuple[list[list[float]], str]:
        """One paid request for `texts`; returns the vectors in order and the ledger op id."""
        op_id = f"{context.run_id}:{secrets.token_hex(6)}"
        request = CallRequest(
            op_id=op_id,
            budget=context.budget,
            provider=self.provider,
            model=self.model,
            # UTF-8 bytes bound the tokens of a byte-level tokenizer (each token >= 1 byte)
            limits={"input": byte_bound(texts)},
            run_id=context.run_id,
            scenario_id=context.scenario_id,
            config_version=context.config_version,
            data_version=context.data_version,
            timeout_s=max(1.0, min(self.call_timeout_s, context.timeout_s)),
            run_cap_micro_usd=context.run_cap_micro_usd,
        )

        def call(req: CallRequest) -> CallResult:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
                dimensions=self.dimensions,
                encoding_format="float",
                timeout=req.timeout_s,
            )
            return CallResult(response, map_usage(response))

        result = guarded_call(self.ledger, self.pricing, request, call)
        return parse_vectors(result.value, len(texts), self.model, self.dimensions), op_id
