"""Runtime settings, read from environment / `.env`. Secret values are never logged."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "PLAN.md").exists() and (parent / "packages").is_dir():
            return parent
    return Path.cwd()


REPO_ROOT = _repo_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    api_football_key: SecretStr | None = None
    football_data_org_key: SecretStr | None = None
    guardian_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    hf_token: SecretStr | None = None

    # Agents' LLM: `<provider>:<model>` entries tried in order (providers in dugout_agents.llm.PROVIDERS), all
    # open-weight models; entries whose key is missing are skipped. Chosen in docs/backtests/writer_llm.md; the local
    # fine-tuned writer (docs/backtests/writer_finetune.md) comes before qwen2.5:3b, which still serves when it is
    # not installed (Ollama answers 404 and the chain moves on).
    llm_chain: str = "groq:qwen/qwen3.8-27b,groq:openai/gpt-oss-120b,openrouter:poolside/laguna-s-2.1:free,local:dugout-writer-1.5b,local:qwen2.5:3b"
    llm_local_base_url: str | None = None  # Ollama somewhere other than localhost:11434 (e.g. docker compose)

    searxng_url: str | None = None
    pff_data_dir: Path | None = None

    ci: bool = False
    # Wikimedia (and polite crawling generally) requires contact details in the User-Agent.
    user_agent: str = (
        "dugout/0.1 (+https://github.com/delta96040/dugout; open-source, non-commercial football research)"
    )

    cache_dir: Path = REPO_ROOT / ".cache"
    warehouse_path: Path = REPO_ROOT / "data" / "warehouse" / "dugout.duckdb"
    config_dir: Path = REPO_ROOT / "config"

    def secret(self, name: str) -> str | None:
        value = getattr(self, name, None)
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        return raw.strip() or None  # a pasted secret often carries a newline, which breaks the auth header


@lru_cache
def get_settings() -> Settings:
    return Settings()
