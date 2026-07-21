"""Environment-driven configuration for the resume builder."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
# Optional. Raises GitHub rate limits *and* unlocks the richer GraphQL
# contribution data (repositoriesContributedTo + per-repo commit counts).
GITHUB_TOKEN: str | None = os.getenv("GITHUB_TOKEN")

# GitHub ingestion: whether to look beyond owned/org repos for contributions to
# other people's repos (extra search/GraphQL calls — set false to disable), and
# how many such external repos to keep (ranked by contribution volume).
GITHUB_INCLUDE_CONTRIBUTIONS: bool = os.getenv(
    "GITHUB_INCLUDE_CONTRIBUTIONS", "true"
).strip().lower() in ("1", "true", "yes", "on")
GITHUB_MAX_EXTERNAL_REPOS: int = int(os.getenv("GITHUB_MAX_EXTERNAL_REPOS", "15"))

# Private repos (personal and organization) are read only when GITHUB_TOKEN
# belongs to the very username being ingested — never for a third party. Their
# names, descriptions and READMEs then reach the extraction LLM, so set false to
# keep ingestion to public data only.
GITHUB_INCLUDE_PRIVATE: bool = os.getenv(
    "GITHUB_INCLUDE_PRIVATE", "true"
).strip().lower() in ("1", "true", "yes", "on")
# Organization/collaborator repos are kept only when the user actually committed
# to them, which costs one commit probe per candidate repo; these cap the probe
# budget and how many surviving repos are rendered (most recent first).
GITHUB_MAX_CONTRIBUTION_PROBES: int = int(
    os.getenv("GITHUB_MAX_CONTRIBUTION_PROBES", "150")
)
GITHUB_MAX_ORG_REPOS: int = int(os.getenv("GITHUB_MAX_ORG_REPOS", "20"))

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
