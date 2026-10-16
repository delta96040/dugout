import httpx
import pytest
from dugout_agents import llm as llm_mod
from dugout_agents.llm import FallbackLLM, LLMError, OpenAICompatLLM, ScriptedLLM, make_llm
from dugout_pipeline import settings as settings_mod
from dugout_pipeline.settings import Settings


@pytest.fixture
def settings(monkeypatch):
    def use(**kw):
        s = Settings(_env_file=None, **kw)
        monkeypatch.setattr(settings_mod, "get_settings", lambda: s)
        return s

    return use


def test_groq_entry_uses_its_key_and_reasoning_options(settings):
    settings(groq_api_key="k")
    llm = make_llm("groq:openai/gpt-oss-120b")
    assert llm.base_url == "https://api.groq.com/openai/v1" and llm.model == "openai/gpt-oss-120b"
    assert llm.extra == {"reasoning_effort": "low", "include_reasoning": False}
    assert make_llm("groq:qwen/qwen3.8-27b").extra == {"reasoning_effort": "none"}
    assert make_llm("openrouter:poolside/laguna-s-2.1:free") is None  # no OpenRouter key
    assert make_llm("local:qwen2.5:3b", "http://ollama:11434/v1").base_url == "http://ollama:11434/v1"
    with pytest.raises(ValueError):
        make_llm("acme:model")


def test_chain_skips_entries_without_a_key(settings, monkeypatch):
    settings(groq_api_key="k", llm_chain="groq:openai/gpt-oss-120b,openrouter:x/y:free,local:qwen2.5:3b")
    monkeypatch.setattr(llm_mod, "_reachable", lambda llm: True)
    chain = llm_mod.from_settings()
    assert isinstance(chain, FallbackLLM)
    assert [x.name for x in chain.chain] == ["openai/gpt-oss-120b", "qwen2.5:3b"]


def test_request_body_and_fractional_retry_after():
    seen, waits = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, headers={"retry-after": "2.5"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    llm = OpenAICompatLLM("https://api.groq.com/openai/v1", "m", "k", sleep=waits.append, extra={"x": 1})
    llm._http = httpx.Client(transport=httpx.MockTransport(handler), headers=llm._http.headers)
    assert llm.complete("s", "u") == "ok"
    assert waits == [2.5]
    body = seen[-1].read()
    assert b'"x":1' in body.replace(b" ", b"")
    assert seen[-1].headers["authorization"] == "Bearer k"


def _llm(handler, **kw):
    llm = OpenAICompatLLM("https://api.groq.com/openai/v1", "m", "k", **kw)
    llm._http = httpx.Client(transport=httpx.MockTransport(handler))
    return llm


def test_long_rate_limit_falls_through_and_skips_the_model_until_it_clears():
    now = [0.0]
    calls = []

    def limited(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "45"})

    first = _llm(limited, sleep=lambda s: None, max_wait=10, clock=lambda: now[0])
    second = ScriptedLLM(["from the fallback", "again"], name="second")
    chain = FallbackLLM([first, second])
    assert chain.complete("s", "u") == "from the fallback" and chain.used == "second"
    assert chain.complete("s", "u") == "again"  # still blocked: no second request to the limited model
    assert len(calls) == 1
    now[0] = 46.0
    with pytest.raises(LLMError):  # cleared: tried again (and limited again)
        first.complete("s", "u")
    assert len(calls) == 2


def test_short_rate_limit_is_waited_out_and_the_last_model_always_waits():
    waits, n = [], [0]

    def once(request):
        n[0] += 1
        if n[0] == 1:
            return httpx.Response(429, headers={"retry-after": "3"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert _llm(once, sleep=waits.append, max_wait=10).complete("s", "u") == "ok"
    assert waits == [3.0]
    n[0], waits[:] = 0, []
    assert _llm(once, sleep=waits.append).complete("s", "u") == "ok"  # no max_wait: plain retry


def test_only_models_with_a_fallback_fall_through(settings, monkeypatch):
    settings(groq_api_key="k", llm_chain="groq:qwen/qwen3.8-27b,groq:openai/gpt-oss-120b")
    monkeypatch.setattr(llm_mod, "_reachable", lambda llm: True)
    chain = llm_mod.from_settings()
    assert [x.max_wait for x in chain.chain] == [llm_mod.FALLTHROUGH_WAIT, None]
