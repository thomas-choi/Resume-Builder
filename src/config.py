"""Environment-driven configuration for the resume builder."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
GITHUB_TOKEN: str | None = os.getenv("GITHUB_TOKEN")

# LLM provider config (same method as FUND's AgentConfig / get_llm)
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
LLM_API_KEY: str | None = os.getenv("LLM_API_KEY") or ANTHROPIC_API_KEY
# Optional: current Claude models reject non-default sampling params, so
# temperature is only passed to the provider when explicitly set.
LLM_TEMPERATURE: float | None = (
    float(os.environ["LLM_TEMPERATURE"]) if os.getenv("LLM_TEMPERATURE") else None
)
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "8000"))
LLM_BASE_URL: str | None = os.getenv("LLM_BASE_URL")
# Max seconds of inactivity tolerated by the provider client
LLM_STREAM_TIMEOUT_S: int = int(os.getenv("LLM_STREAM_TIMEOUT_S", "90"))

EXTRACTION_MODEL: str = os.getenv("EXTRACTION_MODEL", "claude-haiku-4-5-20251001")
SYNTHESIS_MODEL: str = os.getenv("SYNTHESIS_MODEL", "claude-sonnet-5")
TAILORING_MODEL: str = os.getenv("TAILORING_MODEL", "claude-sonnet-5")
VALIDATION_MODEL: str = os.getenv("VALIDATION_MODEL", "claude-sonnet-5")

DATA_DIR: Path = Path(os.getenv("DATA_DIR", "./data"))

# Directory holding the versioned agent skills (SKILL.md per agent). Skills are
# prompt *content* (reasoning strategies/heuristics), not secrets, and ship in
# the image. A missing dir degrades gracefully to inline-prompt behavior.
SKILLS_DIR: Path = Path(os.getenv("SKILLS_DIR", "./skills"))

# Logging: level name (DEBUG/INFO/WARNING/ERROR); unset LOG_FILE = console only
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE: Path | None = Path(os.environ["LOG_FILE"]) if os.getenv("LOG_FILE") else None

# difflib similarity below which a tailored bullet triggers the LLM cross-check
VALIDATION_SIMILARITY_THRESHOLD: float = float(
    os.getenv("VALIDATION_SIMILARITY_THRESHOLD", "0.55")
)
