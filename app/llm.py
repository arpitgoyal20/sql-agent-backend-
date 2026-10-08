"""Chat model factory. Swap providers with LLM_PROVIDER / LLM_MODEL.

Tests replace the model with `set_llm(fake)`; nodes always go through `get_llm()`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from langchain_core.language_models import BaseChatModel

from app.config import ROOT, get_settings

PROMPTS_DIR = ROOT / "prompts"

_override: BaseChatModel | None = None


def set_llm(model: BaseChatModel | None) -> None:
    """Use `model` for every LLM call (tests); pass None to go back to the configured one."""
    global _override
    _override = model


def get_llm() -> BaseChatModel:
    return _override if _override is not None else _configured_llm()


@lru_cache
def _configured_llm() -> BaseChatModel:
    s = get_settings()
    provider = s.llm_provider.lower()
    if provider in ("google", "gemini"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not s.google_api_key:
            raise RuntimeError("GOOGLE_API_KEY (or GEMINI_API_KEY) is not set")
        # Gemini 3 models are tuned for the default temperature; the validator, not
        # sampling settings, is what keeps output correct.
        extra = {} if s.llm_thinking_budget is None else {"thinking_budget": s.llm_thinking_budget}
        return ChatGoogleGenerativeAI(
            model=s.llm_model, google_api_key=s.google_api_key, max_retries=2, **extra
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI  # pip install langchain-openai

        return ChatOpenAI(model=s.llm_model, temperature=0)
    if provider == "groq":
        from langchain_groq import ChatGroq  # pip install langchain-groq

        return ChatGroq(model=s.llm_model, temperature=0)
    raise ValueError(f"Unknown LLM_PROVIDER: {s.llm_provider}")


@lru_cache
def load_prompt(name: str) -> str:
    """Read prompts/<name>.md; formatted with str.format at call time."""
    return (PROMPTS_DIR / f"{name}.md").read_text()


def render(name: str, **values: object) -> str:
    return load_prompt(name).format(**values)


def prompt_path(name: str) -> Path:
    return PROMPTS_DIR / f"{name}.md"
