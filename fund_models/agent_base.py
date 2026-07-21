"""AgentBase and AgentConfig — shared base classes for all FUND agents."""

from __future__ import annotations

import logging
import os
import time
import traceback
import httpx
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
from langchain_core.outputs import LLMResult
from langchain_core.callbacks import BaseCallbackHandler
from logging import Logger
from datetime import datetime

from typing import Any, Dict


def _mask_sensitive(value: Any, key: str | None = None) -> Any:
    """Recursively mask sensitive values."""
    if isinstance(value, dict):
        return {k: _mask_sensitive(v, k) for k, v in value.items()}

    if isinstance(value, list):
        return [_mask_sensitive(v) for v in value]

    if isinstance(value, tuple):
        return tuple(_mask_sensitive(v) for v in value)

    if key and key.lower() == "api_key":
        if value is None:
            return None

        value = str(value)
        if len(value) <= 10:
            return "*" * len(value)

        return f"{value[:5]}...{value[-5:]}"

    return value


def print_kwargs(kwargs: Dict[str, Any]) -> None:
    """Print kwargs with sensitive fields masked."""
    from pprint import pprint

    pprint(_mask_sensitive(kwargs))


async def astream_collect(runnable, input_data, *, config: Optional[Dict[str, Any]] = None):
    """Stream a LangChain runnable and return the fully-accumulated message.

    Using .astream() instead of .ainvoke() keeps a provider's socket
    read-inactivity timeout scoped to the gap between chunks rather than
    the whole generation, so a single small timeout (AgentConfig.llm_stream_timeout)
    works regardless of total output length or a given vendor's tokens/sec.
    """
    full = None
    async for chunk in runnable.astream(input_data, config=config):
        full = chunk if full is None else full + chunk
    if full is None:
        raise RuntimeError("LLM stream produced no output")
    return full

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AgentConfig:
    """
    Shared configuration for any FUND agent.
    Values are read from environment variables with sensible defaults.
    """

    agent_name: str

    # LLM
    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "openai")
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("LLM_API_KEY", "")
    )
    llm_temperature: float = field(
        default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.7"))
    )
    llm_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "2000"))
    )
    llm_base_url: Optional[str] = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL")
    )
    # Max seconds of *inactivity* (gap between streamed chunks) tolerated
    # before a provider's client aborts the call. Since generation is
    # streamed (see astream_collect below), this is independent of total
    # output length or a given vendor's tokens/sec — one shared value
    # covers every agent and every provider without per-workload tuning.
    llm_stream_timeout: int = field(
        default_factory=lambda: int(os.getenv("LLM_STREAM_TIMEOUT_S", "90"))
    )

    # Service
    api_host: str = field(
        default_factory=lambda: os.getenv("API_HOST", "0.0.0.0")
    )
    api_port: int = field(
        default_factory=lambda: int(os.getenv("API_PORT", "8000"))
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )
    timeout: int = field(
        default_factory=lambda: int(os.getenv("TIMEOUT", "60"))
    )
    retry_attempts: int = field(
        default_factory=lambda: int(os.getenv("RETRY_ATTEMPTS", "3"))
    )

    # Memory
    persist_memory: bool = field(
        default_factory=lambda: os.getenv("PERSIST_MEMORY", "false").lower() == "true"
    )
    memory_dir: str = field(
        default_factory=lambda: os.getenv("MEMORY_DIR", "/app/data/memory")
    )

    # Skills
    skills_dir: Optional[str] = field(
        default_factory=lambda: os.getenv("SKILLS_DIR")
    )

    # Deep Agent
    max_context_messages: int = field(
        default_factory=lambda: int(os.getenv("MAX_CONTEXT_MESSAGES", "50"))
    )
    max_plan_iterations: int = field(
        default_factory=lambda: int(os.getenv("MAX_PLAN_ITERATIONS", "10"))
    )
    enable_self_correction: bool = field(
        default_factory=lambda: os.getenv("ENABLE_SELF_CORRECTION", "true").lower() == "true"
    )

    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)


# ------------------------------------------------------------------
# Callback handler Class
# ------------------------------------------------------------------

class TokensPerSecondHandler(BaseCallbackHandler):
    def __init__(self, logger: Logger, user: str, model: str):
        super().__init__()
        self.logger = logger
        self.start_time = None
        self.first_token_time = None
        self.end_time = None
        self.completion_tokens = 0
        self.input_tokens = 0
        self.user = user
        self.model = model

    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any):
        self.start_time = time.perf_counter()
        self.first_token_time = None
        self.completion_tokens = 0

    def on_llm_new_token(self, token: str, **kwargs: Any):
        if self.first_token_time is None:
            self.first_token_time = time.perf_counter()
        self.completion_tokens += 1

    async def _post_llm_usage(self, agent_name: str, generation_duration: float, tps: float):
        storage_key = os.getenv("STORAGE_AGENT_SECRET", "")
        storage_agent_url = os.getenv("STORAGE_AGENT_URL", "http://storage:8006")
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(f"{storage_agent_url}/storage/llm-usage",
                json={
                    "user": self.user or "anonymous",
                    "agent": agent_name,
                    "generation_time": generation_duration,
                    "completion_tokens": self.completion_tokens,
                    "input_tokens": self.input_tokens,
                    "tokens_per_second": tps,
                    "model": self.model,
                    "llmcall_datetime": datetime.now().isoformat()
                },
                headers={"X-Storage-Key": storage_key})

    def on_llm_end(self, response: LLMResult, **kwargs: Any):
        try:
            self.end_time = time.perf_counter()
            generation_duration = self.end_time - (self.first_token_time or self.start_time)
            if response.llm_output and 'token_usage' in response.llm_output:
                self.logger.info(f"Token usage: {response.llm_output['token_usage']}")
                self.completion_tokens = response.llm_output['token_usage'].get('completion_tokens', self.completion_tokens)
                self.input_tokens = response.llm_output['token_usage'].get('prompt_tokens',0)

            tps = self.completion_tokens / generation_duration if generation_duration > 0 else 0
            self.logger.info(f"Total generation time: {generation_duration:.2f}s")
            self.logger.info(f"Completion tokens: {self.completion_tokens}")
            self.logger.info(f"Tokens per second: {tps:.2f}/s")

            agent_name = self.logger.name
            asyncio.run(self._post_llm_usage(agent_name, generation_duration, tps))
            
        except Exception as e:
            error_string = traceback.format_exc()
            self.logger.error(f"Error in TokensPerSecondHandler: {e}")
            self.logger.error(f"Error details: {error_string}")
    
        
# ---------------------------------------------------------------------------
# Base Agent
# ---------------------------------------------------------------------------


class AgentBase:
    """
    Base class for all FUND agents.

    Provides:
    - Structured logging with agent name prefix
    - Performance monitoring (execution time tracking)
    - Retry helpers
    - Lifecycle hooks (setup / teardown)
    - Working memory (in-process dict) + persistent memory (filesystem)
    - Skills loading and embedding
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._start_time: Optional[float] = None
        self._working_memory: Dict[str, Any] = {}
        self._metrics: Dict[str, Any] = {
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "total_execution_time_s": 0.0,
        }
        self._skills_context: str = ""
        self._skill_registry: list = []

        # Logging
        logging.basicConfig(
            level=getattr(logging, config.log_level.upper(), logging.INFO),
            format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
        )
        self.logger = logging.getLogger(config.agent_name)

        # Load skills using the (potentially overridden) skills_dir property
        self._load_skills()

    @property
    def skills_dir(self) -> Optional[str]:
        """Path to this agent's skills directory.

        Subclasses override this property to return their own skills path,
        independent of the shared AgentConfig.skills_dir value.
        """
        return self.config.skills_dir

    def _load_skills(self) -> None:
        """Load all skills from the skills directory (uses self.skills_dir)."""
        sdir = self.skills_dir
        if not sdir:
            self.logger.debug("No skills_dir configured.")
            return

        try:
            from pathlib import Path
            from fund_models.skills import scan_skills, make_load_skill_tool

            skills_path = Path(sdir)
            if not skills_path.exists():
                self.logger.warning("Skills directory not found: %s", skills_path)
                return

            self._skill_registry = scan_skills(skills_path)
            self.logger.info("Loaded %d skills from %s.", len(self._skill_registry), skills_path)

            # Build skills context string for embedding in prompts
            skills_text = "## Available Skills\n\n"
            for skill in self._skill_registry:
                skills_text += f"### {skill['name']}\n{skill['description']}\n\n"
            self._skills_context = skills_text

            # Register the load_skill tool if DeepAgentMixin is mixed in
            if hasattr(self, "register_tool") and self._skill_registry:
                load_tool = make_load_skill_tool(self._skill_registry)
                self.register_tool(load_tool)  # type: ignore[attr-defined]

            self.logger.debug("Skills context:\n%s", self._skills_context)
        except Exception as exc:
            self.logger.error("Failed to load skills: %s", exc)

    def get_skills_context(self) -> str:
        """Return the formatted skills context for embedding in prompts."""
        return self._skills_context

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Called once before the agent starts serving requests."""
        self.logger.info("Agent %r starting up.", self.config.agent_name)

    def teardown(self) -> None:
        """Called on graceful shutdown."""
        self.logger.info(
            "Agent %r shutting down. Metrics: %s",
            self.config.agent_name,
            self._metrics,
        )

    # ------------------------------------------------------------------
    # Performance monitoring
    # ------------------------------------------------------------------

    def start_run(self) -> None:
        self._start_time = time.perf_counter()
        self._metrics["total_runs"] += 1

    def end_run(self, success: bool = True) -> float:
        elapsed = time.perf_counter() - (self._start_time or time.perf_counter())
        self._metrics["total_execution_time_s"] += elapsed
        if success:
            self._metrics["successful_runs"] += 1
        else:
            self._metrics["failed_runs"] += 1
        self._start_time = None
        return elapsed

    def get_metrics(self) -> Dict[str, Any]:
        return dict(self._metrics)

    # ------------------------------------------------------------------
    # Working memory (short-term, in-process)
    # ------------------------------------------------------------------

    def remember(self, key: str, value: Any) -> None:
        self._working_memory[key] = value

    def recall(self, key: str, default: Any = None) -> Any:
        return self._working_memory.get(key, default)

    def forget(self, key: str) -> None:
        self._working_memory.pop(key, None)

    # ------------------------------------------------------------------
    # Retry helper
    # ------------------------------------------------------------------

    def with_retry(self, fn, *args, max_attempts: Optional[int] = None, **kwargs):
        """Call *fn* with *args*/**kwargs*, retrying on exception."""
        attempts = max_attempts or self.config.retry_attempts
        last_exc: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                self.logger.warning(
                    "Attempt %d/%d failed: %s", attempt, attempts, exc
                )
                time.sleep(2 ** (attempt - 1))  # exponential back-off
        raise RuntimeError(
            f"All {attempts} attempts failed. Last error: {last_exc}"
        ) from last_exc
        

    # ------------------------------------------------------------------
    # LLM factory
    # ------------------------------------------------------------------

    def get_llm(self):
        """
        Return a LangChain-compatible LLM instance configured from
        AgentConfig.  Supports openai, anthropic, and google providers.
        """
        provider = self.config.llm_provider.lower()

        if provider == "openai":
            from langchain_openai import ChatOpenAI  # type: ignore
            kwargs: Dict[str, Any] = {
                "model": self.config.llm_model,
                "api_key": self.config.llm_api_key,
                "temperature": self.config.llm_temperature,
                "max_tokens": self.config.llm_max_tokens,
                "timeout": self.config.llm_stream_timeout,
            }
            if self.config.llm_base_url:
                kwargs["base_url"] = self.config.llm_base_url
            self.logger.info("openai: kwargs: %s", kwargs)
            return ChatOpenAI(**kwargs)
        elif provider == "anthropic":
            from langchain_anthropic import ChatAnthropic  # type: ignore
            anthropic_kwargs: Dict[str, Any] = {
                "model": self.config.llm_model,
                "api_key": self.config.llm_api_key,
                "temperature": self.config.llm_temperature,
                "max_tokens": self.config.llm_max_tokens,
                "timeout": self.config.llm_stream_timeout,
            }
            if self.config.llm_base_url:
                anthropic_kwargs["base_url"] = self.config.llm_base_url
            self.logger.info("anthropic: kwargs: %s", anthropic_kwargs)
            return ChatAnthropic(**anthropic_kwargs)
        elif provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore
            google_kwargs: Dict[str, Any] = {
                "model": self.config.llm_model,
                "api_key": self.config.llm_api_key,
                "temperature": self.config.llm_temperature,
                "max_tokens": self.config.llm_max_tokens,
                "timeout": self.config.llm_stream_timeout,
            }
            self.logger.info("google: kwargs: %s", google_kwargs)
            return ChatGoogleGenerativeAI(**google_kwargs)
        elif provider == "nvidia":
            from langchain_nvidia_ai_endpoints import ChatNVIDIA  # type: ignore
            kwargs: Dict[str, Any] = {
                "model": self.config.llm_model,
                "api_key": self.config.llm_api_key,
                "temperature": self.config.llm_temperature,
                "max_completion_tokens": self.config.llm_max_tokens,
                "timeout": self.config.llm_stream_timeout,
            }
            if self.config.llm_base_url:
                kwargs["base_url"] = self.config.llm_base_url
            self.logger.info("nvidia: kwargs: %s", _mask_sensitive(kwargs))
            return ChatNVIDIA(**kwargs)
        elif provider == "llamacpp":
            from langchain_openai import ChatOpenAI  # type: ignore
            kwargs: Dict[str, Any] = {
                "model": self.config.llm_model,
                "temperature": self.config.llm_temperature,
                "max_tokens": self.config.llm_max_tokens,
                "api_key": self.config.llm_api_key or "none",
                "base_url": self.config.llm_base_url or "http://localhost:8080/v1",
                "timeout": self.config.llm_stream_timeout,
            }
            self.logger.info("llamacpp: kwargs: %s", _mask_sensitive(kwargs))
            return ChatOpenAI(**kwargs)
        elif provider == "deepseek":
            from langchain_deepseek import ChatDeepSeek  # type: ignore
            llm = ChatDeepSeek(
                model=self.config.llm_model,
                api_key=self.config.llm_api_key,
                temperature=0.7,
                max_tokens=None,
                timeout=None,
                max_retries=2
            )
            self.logger.info("deepseek: llm: %s", _mask_sensitive(llm))
            return llm
        elif provider == "openrouter":
            from langchain_openrouter import ChatOpenRouter  # type: ignore
            kwargs: Dict[str, Any] = {
                "model": self.config.llm_model,
                "temperature": self.config.llm_temperature,
                "max_tokens": self.config.llm_max_tokens,
                "api_key": self.config.llm_api_key,
                "base_url": self.config.llm_base_url or "https://openrouter.ai/api/v1",
                "timeout": self.config.llm_stream_timeout,
            }
            self.logger.info("openrouter: kwargs: %s", kwargs)
            return ChatOpenRouter(**kwargs)
        else:
            raise ValueError(
                f"Unsupported LLM provider {provider!r}. "
                "Supported: openai, anthropic, google, nvidia, llamacpp, deepseek, openrouter."
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.config.agent_name!r})"