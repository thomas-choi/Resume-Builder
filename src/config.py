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
# A GitHub source is one document holding every repo, which at ~50 repos asks
# the extractor for more structured output than it reliably returns (observed:
# a response with no tool call at all, losing the whole source). Repos are
# extracted this many at a time; smaller = more calls, less truncation risk.
GITHUB_REPOS_PER_EXTRACTION: int = int(os.getenv("GITHUB_REPOS_PER_EXTRACTION", "10"))

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
# The cover letter reuses the tailoring tier unless overridden — it is the same
# "re-frame profile facts for one posting" task, under the same no-fabrication rules.
COVER_LETTER_MODEL: str = os.getenv("COVER_LETTER_MODEL", TAILORING_MODEL)

DATA_DIR: Path = Path(os.getenv("DATA_DIR", "./data"))

# Document rendering (Phase 3). The renderer is pure Python (python-docx); PDF
# is produced by converting the rendered .docx with headless LibreOffice, which
# is installed in the Docker image but optional locally — a missing binary
# degrades to "docx only" with a WARNING, never a failed tailoring run.
DOCX_TEMPLATE: Path | None = (
    Path(os.environ["DOCX_TEMPLATE"]) if os.getenv("DOCX_TEMPLATE") else None
)
RENDER_PDF: bool = os.getenv("RENDER_PDF", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
LIBREOFFICE_BIN: str = os.getenv("LIBREOFFICE_BIN", "soffice")
LIBREOFFICE_TIMEOUT_S: int = int(os.getenv("LIBREOFFICE_TIMEOUT_S", "120"))

# Human review (Phase 4). The review agent only *explains* the validation flags
# to the person deciding on them, so it reuses the validation tier; set
# REVIEW_AGENT_ENABLED=false to pause for review without spending an LLM call
# (the flags themselves, not the brief, are what gate rendering).
REVIEW_MODEL: str = os.getenv("REVIEW_MODEL", VALIDATION_MODEL)
REVIEW_AGENT_ENABLED: bool = os.getenv(
    "REVIEW_AGENT_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
# Bound on the review agent's tool-calling loop (it loads skill bodies on
# demand); exceeding it yields no brief rather than looping.
REVIEW_MAX_TOOL_ITERATIONS: int = int(os.getenv("REVIEW_MAX_TOOL_ITERATIONS", "4"))

# Built frontend (Phase 4). When this directory holds an index.html the API
# serves the review UI at `/`; otherwise `/` redirects to the API docs, so a
# backend-only checkout (or `uvicorn` before `npm run build`) still works.
FRONTEND_DIR: Path = Path(os.getenv("FRONTEND_DIR", "./frontend/dist"))

# Directory holding the versioned agent skills (SKILL.md per agent). Skills are
# prompt *content* (reasoning strategies/heuristics), not secrets, and ship in
# the image. A missing dir degrades gracefully to inline-prompt behavior.
SKILLS_DIR: Path = Path(os.getenv("SKILLS_DIR", "./skills"))

# Accounts / passwordless auth (Phase 7). 7.a adds only the mail rows; the
# auth-flow rows (verify method, TTLs, session cookie, rate limit) arrive with
# 7.b. `AUTH_ENABLED` / `SINGLE_USER_EMAIL` are deliberately deferred to 7.c —
# they only matter once the stores take a per-user root.


def _flag(name: str, default: str = "true") -> bool:
    """Parse a boolean env var the same way as RENDER_PDF."""
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# Mail delivery (§14.9). `file` (default) drops a complete .eml into the outbox
# so the whole sign-up/sign-in flow is exercisable with no SMTP server;
# `console` logs the code/link; `smtp` sends for real via stdlib smtplib.
EMAIL_BACKEND: str = os.getenv("EMAIL_BACKEND", "file")
EMAIL_FROM: str = os.getenv("EMAIL_FROM", "no-reply@localhost")
EMAIL_OUTBOX_DIR: Path = Path(os.getenv("EMAIL_OUTBOX_DIR", "./data/auth/outbox"))
SMTP_HOST: str | None = os.getenv("SMTP_HOST")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str | None = os.getenv("SMTP_USER")
SMTP_PASSWORD: str | None = os.getenv("SMTP_PASSWORD")
SMTP_STARTTLS: bool = _flag("SMTP_STARTTLS", "true")
SMTP_TIMEOUT_S: int = int(os.getenv("SMTP_TIMEOUT_S", "10"))

# Auth flow (§14.10). Added in 7.b; NOT AUTH_ENABLED/SINGLE_USER_EMAIL (7.c).
AUTH_VERIFY_METHOD: str = os.getenv("AUTH_VERIFY_METHOD", "code")  # code | link
VERIFY_CODE_LENGTH: int = int(os.getenv("VERIFY_CODE_LENGTH", "6"))
AUTH_MAX_CODE_ATTEMPTS: int = int(os.getenv("AUTH_MAX_CODE_ATTEMPTS", "5"))
# Base for magic links + the "you already have an account" mail. Never derived
# from the request Host header (a forged Host would point the link elsewhere).
PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "rb_session")
SESSION_COOKIE_SECURE: bool = _flag("SESSION_COOKIE_SECURE", "true")
SESSION_TTL_S: int = int(os.getenv("SESSION_TTL_S", "1209600"))  # 14 days, sliding
SIGNUP_TTL_S: int = int(os.getenv("SIGNUP_TTL_S", "1800"))  # 30 min
SIGNIN_TTL_S: int = int(os.getenv("SIGNIN_TTL_S", "900"))  # 15 min
AUTH_MAX_SENDS_PER_HOUR: int = int(os.getenv("AUTH_MAX_SENDS_PER_HOUR", "5"))

# Logging: level name (DEBUG/INFO/WARNING/ERROR); unset LOG_FILE = console only
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE: Path | None = Path(os.environ["LOG_FILE"]) if os.getenv("LOG_FILE") else None

# difflib similarity below which a tailored bullet triggers the LLM cross-check
VALIDATION_SIMILARITY_THRESHOLD: float = float(
    os.getenv("VALIDATION_SIMILARITY_THRESHOLD", "0.55")
)
