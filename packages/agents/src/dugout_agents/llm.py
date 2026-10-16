"""LLM access: any OpenAI-compatible chat endpoint serving an open-weight model.

`LLM_CHAIN` lists `<provider>:<model>` entries tried in order (see PROVIDERS): free hosted open-weight models
(Groq, OpenRouter) first, a local Ollama model last. Keys come from .env under each provider's own name and are
sent only as the Authorization header of that endpoint; they are never logged.

The LLM only ever turns fact cards into prose. It never sees a number (facts carry placeholders, not values),
and its output is rejected unless it passes the same checks as the Stat Auditor.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class LLMError(RuntimeError):
    pass


# provider -> (OpenAI-compatible base URL, settings field holding its key or None)
PROVIDERS: dict[str, tuple[str, str | None]] = {
    "groq": ("https://api.groq.com/openai/v1", "groq_api_key"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_api_key"),
    "local": ("http://localhost:11434/v1", None),  # Ollama; LLM_LOCAL_BASE_URL overrides (e.g. in docker)
}


def extra_body(provider: str, model: str) -> dict:
    """Reasoning models: think briefly and keep the reasoning out of the reply (it counts toward max_tokens)."""
    if provider == "groq" and model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "include_reasoning": False}
    if provider == "groq" and model.startswith("qwen/qwen3"):
        return {"reasoning_effort": "none"}
    return {}


def _retry_after(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


FALLTHROUGH_WAIT = 10.0  # seconds a model in front of a fallback may be asked to wait before we move on


class OpenAICompatLLM:
    RETRY_STATUS = (429, 500, 502, 503, 504)

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 180.0,
        temperature: float = 0.2,
        max_tokens: int = 600,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        extra: dict | None = None,
        max_wait: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.base_url, self.model = base_url.rstrip("/"), model
        self.name = model
        self.temperature, self.max_tokens, self.retries, self._sleep = temperature, max_tokens, retries, sleep
        self.extra = extra or {}
        # With a fallback behind it, a model asked to wait longer than `max_wait` seconds (a per-minute token cap,
        # a daily quota) is skipped until then instead of slept on; None = always wait (the last in the chain).
        self.max_wait, self._clock, self.blocked_until = max_wait, clock, 0.0
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        if "openrouter.ai" in self.base_url:  # OpenRouter asks apps to identify themselves
            headers |= {"HTTP-Referer": "https://github.com/delta96040/dugout", "X-Title": "dugout"}
        self._http = httpx.Client(timeout=timeout, headers=headers)
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "seed": 7,
            **self.extra,
        }
        if self._clock() < self.blocked_until:
            raise LLMError(f"{self.model}: rate-limited, skipped")
        for attempt in range(self.retries + 1):
            try:
                self.calls += 1
                r = self._http.post(f"{self.base_url}/chat/completions", json=body)
                if r.status_code in self.RETRY_STATUS and attempt < self.retries:
                    wait = _retry_after(r.headers.get("retry-after", ""))
                    if (
                        r.status_code == 429
                        and self.max_wait is not None
                        and (wait is None or wait > self.max_wait)
                    ):
                        self.blocked_until = self._clock() + (wait if wait is not None else 60.0)
                        raise LLMError(f"{self.model}: rate-limited, falling through")
                    self._sleep(min(wait, 60.0) if wait is not None else 5 * 2**attempt)
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"] or ""
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                if attempt < self.retries and isinstance(exc, httpx.TransportError):
                    self._sleep(5 * 2**attempt)
                    continue
                raise LLMError(
                    f"{self.model}: {type(exc).__name__}"
                ) from exc  # no URL/headers in the message
        raise LLMError(f"{self.model}: still rate-limited after {self.retries} retries")


@dataclass
class ScriptedLLM:
    """Deterministic stand-in for tests: replies from a list (or a function of the prompt)."""

    replies: list[str] | Callable[[str, str], str] = field(default_factory=list)
    name: str = "scripted"
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if callable(self.replies):
            return self.replies(system, user)
        if not self.replies:
            raise LLMError("script exhausted")
        return self.replies.pop(0)


@dataclass
class FallbackLLM:
    """Tries each LLM in turn: a hosted model first, a local one when the hosted one errors or is rate-limited."""

    chain: list[LLM]

    @property
    def name(self) -> str:
        return self.chain[0].name

    def complete(self, system: str, user: str) -> str:
        last: LLMError | None = None
        for llm in self.chain:
            try:
                text = llm.complete(system, user)
                self.used = llm.name
                return text
            except LLMError as exc:
                last = exc
        raise last or LLMError("no LLM configured")


def _reachable(llm: OpenAICompatLLM) -> bool:
    for _ in range(2):  # a local server may still be loading its model: be patient once
        try:
            httpx.get(f"{llm.base_url}/models", timeout=30, headers=llm._http.headers).raise_for_status()
            return True
        except httpx.HTTPError:
            continue
    return False


def make_llm(
    spec: str, local_base_url: str | None = None, max_wait: float | None = None
) -> OpenAICompatLLM | None:
    """`<provider>:<model>`, e.g. `groq:openai/gpt-oss-120b`. None when the provider's key is not set."""
    from dugout_pipeline.settings import get_settings

    provider, model = spec.strip().split(":", 1)
    if provider not in PROVIDERS:
        raise ValueError(f"unknown LLM provider {provider!r} (known: {', '.join(PROVIDERS)})")
    base, key_name = PROVIDERS[provider]
    if provider == "local" and local_base_url:
        base = local_base_url
    key = get_settings().secret(key_name) if key_name else None
    if key_name and not key:
        return None
    return OpenAICompatLLM(base, model, key, extra=extra_body(provider, model), max_wait=max_wait)


def from_settings() -> LLM | None:
    """The configured LLM chain (`LLM_CHAIN`), or None when nothing is reachable (templates only).

    Chosen on docs/backtests/writer_llm.md; every entry serves open weights. Entries without a key are skipped."""
    from dugout_pipeline.settings import get_settings

    s = get_settings()
    chain: list[LLM] = []
    for spec in filter(None, s.llm_chain.split(",")):
        llm = make_llm(spec, s.llm_local_base_url)
        if llm is not None and _reachable(llm):
            chain.append(llm)
    if not chain:
        log.warning("no LLM endpoint reachable; using deterministic templates only")
        return None
    for llm in chain[:-1]:  # each free model has its own rate limit: move on rather than sleep
        llm.max_wait = FALLTHROUGH_WAIT
    return chain[0] if len(chain) == 1 else FallbackLLM(chain)
