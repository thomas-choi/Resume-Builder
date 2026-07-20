"""Provider-switched LLM factory (single mock point for tests).

Same method as FUND's ``AgentBase.get_llm()`` (fund_models/agent_base.py):
a provider switch with lazy per-provider imports, configured from environment
variables. Unlike FUND, ``model`` and ``max_tokens`` stay per-call arguments
because this project tiers models per pipeline stage (extraction vs synthesis
vs validation).
"""

import logging
from typing import Any

from src import config

logger = logging.getLogger(__name__)


def _mask_sensitive(kwargs: dict[str, Any]) -> dict[str, Any]:
    masked = dict(kwargs)
    if masked.get("api_key"):
        masked["api_key"] = "***"
    return masked


def make_llm(model: str, max_tokens: int | None = None):
    """Return a LangChain-compatible chat model configured from src.config.

    Args:
        model: Model id for the selected provider (from src.config tiering).
        max_tokens: Output token cap; defaults to LLM_MAX_TOKENS.

    Returns:
        A LangChain chat model for the provider in LLM_PROVIDER.

    Raises:
        ValueError: If LLM_PROVIDER is not supported.
    """
    provider = config.LLM_PROVIDER.lower()

    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": config.LLM_API_KEY,
        "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
        "timeout": config.LLM_STREAM_TIMEOUT_S,
    }
    # Current Claude models (Sonnet 5 / Haiku 4.5 tiering defaults) reject
    # non-default sampling params, so temperature is sent only when set.
    if config.LLM_TEMPERATURE is not None:
        kwargs["temperature"] = config.LLM_TEMPERATURE
    if config.LLM_BASE_URL:
        kwargs["base_url"] = config.LLM_BASE_URL

    if provider == "openai":
        from langchain_openai import ChatOpenAI  # type: ignore

        logger.info("openai: kwargs: %s", _mask_sensitive(kwargs))
        return ChatOpenAI(**kwargs)
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # type: ignore

        logger.info("anthropic: kwargs: %s", _mask_sensitive(kwargs))
        return ChatAnthropic(**kwargs)
    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore

        kwargs.pop("base_url", None)
        logger.info("google: kwargs: %s", _mask_sensitive(kwargs))
        return ChatGoogleGenerativeAI(**kwargs)
    elif provider == "nvidia":
        from langchain_nvidia_ai_endpoints import ChatNVIDIA  # type: ignore

        kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
        logger.info("nvidia: kwargs: %s", _mask_sensitive(kwargs))
        return ChatNVIDIA(**kwargs)
    elif provider == "llamacpp":
        from langchain_openai import ChatOpenAI  # type: ignore

        kwargs["api_key"] = kwargs["api_key"] or "none"
        kwargs.setdefault("base_url", "http://localhost:8080/v1")
        logger.info("llamacpp: kwargs: %s", _mask_sensitive(kwargs))
        return ChatOpenAI(**kwargs)
    elif provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek  # type: ignore

        # DeepSeek thinking-mode models (e.g. deepseek-v4-flash/-pro) reject
        # the forced tool_choice that with_structured_output sends ("Thinking
        # mode does not support this tool_choice"), and every pipeline stage
        # relies on structured output.
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        logger.info("deepseek: kwargs: %s", _mask_sensitive(kwargs))
        return ChatDeepSeek(**kwargs)
    elif provider == "openrouter":
        from langchain_openrouter import ChatOpenRouter  # type: ignore

        kwargs.setdefault("base_url", "https://openrouter.ai/api/v1")
        logger.info("openrouter: kwargs: %s", _mask_sensitive(kwargs))
        return ChatOpenRouter(**kwargs)
    else:
        raise ValueError(
            f"Unsupported LLM provider {provider!r}. "
            "Supported: openai, anthropic, google, nvidia, llamacpp, deepseek, openrouter."
        )
