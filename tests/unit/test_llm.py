"""Provider-switched LLM factory."""

import pytest

from src import config
from src.agents import llm


def test_anthropic_provider_default(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    model = llm.make_llm("claude-sonnet-5", max_tokens=1234)
    assert type(model).__name__ == "ChatAnthropic"
    assert model.max_tokens == 1234


def test_max_tokens_defaults_from_config(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_MAX_TOKENS", 4321)
    model = llm.make_llm("claude-sonnet-5")
    assert model.max_tokens == 4321


def test_temperature_omitted_unless_set(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_TEMPERATURE", None)
    assert llm.make_llm("claude-sonnet-5").temperature is None
    monkeypatch.setattr(config, "LLM_TEMPERATURE", 0.3)
    assert llm.make_llm("claude-sonnet-5").temperature == 0.3


def test_deepseek_disables_thinking(monkeypatch):
    pytest.importorskip("langchain_deepseek")
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    model = llm.make_llm("deepseek-v4-flash")
    assert type(model).__name__ == "ChatDeepSeek"
    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_unsupported_provider_raises(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "mystery")
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        llm.make_llm("some-model")


def test_mask_sensitive_hides_api_key():
    masked = llm._mask_sensitive({"api_key": "sk-secret", "model": "m"})
    assert masked["api_key"] == "***"
    assert masked["model"] == "m"
