# Implementation Plan — Career Profile & Targeted CV Generator (all phases)

## Context

The repo is a fresh scaffold containing only `TECHNICAL-DESIGN.md`, which specifies a two-stage LangGraph agent pipeline: (1) ingest career sources (CV docx/PDF, GitHub, LinkedIn export, free text) into a canonical `CareerProfile`; (2) given a job post, generate a tailored CV (optionally a cover letter) with an anti-fabrication validation gate, rendered to docx/PDF, reviewed by a human before final output. No source code exists yet.

Decisions agreed with the user:
- **FastAPI from day one**, all agents in **one Docker container**.
- **Storage: versioned JSON files** (single-user), no Postgres.
- Phasing follows the design doc's build order (§12): core pipeline first, then LinkedIn, rendering, and UI as subsequent phases — **this plan covers all phases through the complete design doc**.

Branch per phase: `feat/core-pipeline`, `feat/linkedin-ingest`, `feat/document-render`, `feat/review-ui` (never commit to main).

## Architecture decisions (apply to all phases)

- **Two LangGraph `StateGraph`s** sharing one schema module, since the stages run at different times:
  - **Ingestion graph:** `ingest_sources → extract_source (per source) → synthesize_profile → store_profile`
  - **Tailoring graph:** `analyze_job → tailor_cv → validate_cv` (Phase 3 adds `render_document`; Phase 4 adds a human-review `interrupt()` before it)
  - State is a `TypedDict` at the top of each graph file; node names are verbs.
- **Deterministic parsing before LLM**: docx/PDF/GitHub/LinkedIn ingestion nodes are pure Python. LLM calls happen only in extraction, synthesis, job-analysis, tailoring, validation.
- **Model tiering** (env-configurable defaults): Haiku (`claude-haiku-4-5-20251001`) for per-source extraction; Sonnet (`claude-sonnet-5`) for synthesis, job analysis, tailoring; validation defaults to Sonnet with env override to Opus (`claude-opus-4-8`).
- **Structured output**: every LLM node uses `ChatAnthropic(...).with_structured_output(<PydanticModel>)` — no free-form JSON parsing.
- **Traceability**: every `Experience`/`Project`/`Skill` carries `source`; synthesis populates `raw_source_map` (claim → source doc id). Cross-source conflicts go into a `CareerProfile.conflicts` list — surfaced, never silently resolved.
- **Validation gate**: (a) non-LLM check — every `TailoredCV` bullet/skill must map to a profile entry via `raw_source_map`, with difflib similarity vs. the original bullet; (b) LLM cross-check on anything below threshold. Flags returned as `needs_review`. In Phases 1–3 human review is client-side; Phase 4 upgrades it to a LangGraph `interrupt()` with checkpointer, per design doc §8.

## File layout (end state)

```
requirements.txt, .env.example, Dockerfile, docker-compose.yml, PLAN.md
src/
├── config.py                  # env loading (python-dotenv), model names, data dir
├── models/schemas.py          # SourceDocument, Experience, Project, Skill, CareerProfile,
│                              # JobRequirements, TailoredCV, ValidationResult, CoverLetter
├── tools/
│   ├── docx_reader.py         # python-docx, headers as section boundaries
│   ├── pdf_reader.py          # pdfplumber; per-page extraction; two-column caveat documented
│   ├── github_client.py       # httpx vs api.github.com: repos, languages, README excerpts (truncated)
│   ├── linkedin_export.py     # Phase 2: parse official data-export ZIP (Positions/Education/Skills/Certifications CSVs)
│   └── docx_renderer.py       # Phase 3: python-docx template rendering (+ PDF via LibreOffice headless)
├── agents/
│   ├── ingestion_graph.py     # IngestionState TypedDict + StateGraph wiring
│   ├── tailoring_graph.py     # TailoringState TypedDict + StateGraph wiring (+checkpointer in Phase 4)
│   ├── extraction.py          # extract_source node (Haiku)
│   ├── synthesis.py           # synthesize_profile node (Sonnet): dedupe, conflict surfacing, narrative
│   ├── job_analysis.py        # analyze_job node → JobRequirements
│   ├── tailoring.py           # tailor_cv node → TailoredCV (hard no-fabrication rules in system prompt)
│   ├── validation.py          # validate_cv node: source-map check + similarity + LLM cross-check
│   └── document.py            # Phase 3: render_document node (no LLM)
├── chains/prompts/            # one prompt-template module per LLM agent
├── utils/
│   ├── profile_store.py       # versioned JSON store: data/profiles/{profile_id}/v{n}.json + latest pointer
│   ├── run_store.py           # per-run provenance: data/sources/{run_id}/ + data/output/{run_id}/output.json + manifest
│   └── logging_setup.py       # root logging config + run_id log correlation (contextvar)
└── api/
    ├── main.py                # FastAPI app factory; serves built frontend in Phase 4
    └── routes.py
frontend/                      # Phase 4: React + Vite + TanStack
tests/
├── conftest.py                # fixtures: sample docx/pdf/LinkedIn-export files, mocked LLMs, tmp data dir
├── unit/                      # no real API calls; LLMs mocked
└── integration/               # @pytest.mark.integration; real Anthropic/GitHub APIs
data/
├── profiles/                  # gitignored runtime storage (versioned profiles)
├── sources/{run_id}/          # archived raw inputs per ingest run (cv/, github.json, linkedin-summary.txt, manifest.json)
└── output/{run_id}/output.json  # copy of the synthesized profile per run
```

## Environment variables (`.env.example`, kept in sync every phase)

```
ANTHROPIC_API_KEY=sk-...
GITHUB_TOKEN=ghp-...            # optional, raises GitHub rate limits
EXTRACTION_MODEL=claude-haiku-4-5-20251001
SYNTHESIS_MODEL=claude-sonnet-5
TAILORING_MODEL=claude-sonnet-5
VALIDATION_MODEL=claude-sonnet-5
DATA_DIR=./data
```

---

## Phase 1 — Core pipeline + FastAPI + Docker (design doc §12 steps 1–4)

1. **Scaffold** — branch `feat/core-pipeline`; `.venv`; `requirements.txt` (langgraph, langchain-anthropic, fastapi, uvicorn, python-docx, pdfplumber, httpx, python-dotenv, pydantic, sse-starlette, pytest, pytest-mock, pytest-asyncio); `.env.example`; verify `.gitignore` covers `.env`, `.venv/`, `data/`.
2. **Schemas** — `src/models/schemas.py` exactly per design doc §4/§6/§7, plus `ValidationResult` and `CareerProfile.conflicts`.
3. **Ingestion tools** — docx/pdf readers, GitHub client, free-text passthrough → `SourceDocument` objects.
4. **Extraction + synthesis agents**, ingestion graph wiring, `profile_store`.
5. **Job analysis, tailoring, validation agents**, tailoring graph wiring.
6. **FastAPI layer + Docker** — endpoints below; SSE via in-process job registry + `asyncio.Queue`; `Dockerfile` (python:3.11-slim, uvicorn on `0.0.0.0`) + `docker-compose.yml` (one service, `.env`, `data/` volume).

| Endpoint | Behavior |
|---|---|
| `POST /ingest` | multipart: CV file(s) + optional `github_username` + optional `free_text` → runs ingestion graph → `run_id` + `profile_id` + `CareerProfile` (incl. `conflicts`); archives raw inputs + output copy per `run_id` |
| `GET /ingest/{job_id}/events` | SSE per-node progress |
| `GET /profile/{profile_id}` | latest version; `?version=n` for specific |
| `PUT /profile/{profile_id}` | save user-edited profile as new version (v1 conflict resolution) |
| `POST /tailor` | `profile_id` + job post text → `TailoredCV` + `ValidationResult` |
| `GET /healthz` | liveness |

**Tests:** `tests/unit/` — schemas, docx/pdf readers (fixture files), github client (mocked httpx), extraction/synthesis (mocked LLM; dedupe + conflict logic), tailoring (subset-of-profile invariant), **validation (key suite: fabricated bullet → flagged; reworded-but-sourced → passes; unsourced skill → flagged)**, profile_store versioning, API via `TestClient` with graphs mocked. `tests/integration/test_pipeline.py` — real end-to-end on a sample CV.

#### Phase 1.a — Run tracking & provenance (`run_id`)

An enhancement to Phase 1 that gives end-to-end traceability between pipeline steps. Previously
`/ingest` kept no record of its inputs — uploaded CVs were parsed from a temp file then deleted,
GitHub/free-text were discarded, and only the final profile was stored; neither `job_id` (SSE
progress) nor `profile_id` (storage key) tied raw inputs to the produced output.

- **`run_id`** — one correlation id per `/ingest` execution. Reuses the `job_id` (they are 1:1);
  generated when the client doesn't pass one. Distinct from `profile_id` (which evolves across runs).
- **`src/utils/run_store.py`** (mirrors `profile_store.py`) archives, keyed by `run_id`:
  - `data/sources/{run_id}/cv/<original-name>` — raw uploaded CV bytes, persisted **before** parsing
    (so inputs survive even if the graph later fails). Filenames are sanitized (`Path(name).name`).
  - `data/sources/{run_id}/github/github.json` — the serialized GitHub `SourceDocument`.
  - `data/sources/{run_id}/linkedin/linkedin-summary.txt` — the free-text input. **LinkedIn is mapped
    through the existing `free_text` path** and archived here; no dedicated LinkedIn ingestion is built
    (that remains Phase 2 below).
  - `data/sources/{run_id}/manifest.json` — index of the above (category, filename, size, sha256),
    linked to the produced `profile_id`/`version`.
  - `data/output/{run_id}/output.json` — a copy of the synthesized profile, written by `store_profile`.
- **Log correlation** — a `contextvars` `run_id` field (`logging_setup.py`) tags every node's log line
  with `[run:<run_id>]`, so a run is greppable across steps.
- **Schema** — `SourceDocument.stored_path` (optional) links a source back to its archived raw file.
- **Retention note** — raw CVs are now **retained** where they were previously deleted; documented in
  `OPERATIONS.md`. `data/` remains gitignored.

**Tests:** `tests/unit/test_run_store.py` (save/sanitize/manifest/output roundtrip); extended
`test_api.py` (asserts `sources/{run_id}/` files, `manifest.json`, and `run_id` in the response);
extended `test_graphs.py` (`store_profile` writes `output/{run_id}/output.json`); extended
`test_logging_setup.py` (`[run:...]` tag).

#### Phase 1.b — Enhance agents to use `SKILL.md` (FUND skills mechanism)

An enhancement to Phase 1 that moves each agent's hand-tuned reasoning guidance
(tailoring heuristics, anti-fabrication rules, synthesis dedupe/conflict strategy)
out of hardcoded prompt strings and into versioned, discoverable **skills** —
reusing FUND's skill machinery verbatim, the same way `src/agents/llm.py` and
`src/config.py` already mirror `AgentConfig`/`get_llm`. Skills hold *reasoning*
(strategies, heuristics, guidelines) — never actions — matching the contract in
`fund_models/skills.py`'s `load_skill_from_fs` docstring.

- **Reuse `fund_models/skills.py` unchanged** — vendor it (already present, untracked)
  as a package: add `fund_models/__init__.py`, make it importable (installed/`PYTHONPATH`),
  and pin `pyyaml` in `requirements.txt` (imported by `skills.py`). No edits to the file —
  Phase 1.b consumes only `scan_skills()`. `fund_models/agent_base.py` is **out of scope
  here**: this repo's agents are LangGraph node functions calling module-level `make_llm`
  (`src/agents/llm.py` already mirrors `AgentBase.get_llm`), not `AgentBase` subclasses, so
  nothing in Phase 1 instantiates `AgentBase`. Adopting the `AgentBase` class (and with it
  `_load_skills`/`get_skills_context`/`register_tool` + the runtime `make_load_skill_tool`
  tool) is deferred to Phase 4, where a tool-calling/DeepAgent node can actually use it.
- **`skills/` directory** at repo root — the migration **authors a basic starter
  `SKILL.md` for every one of the five Phase 1 agents**, so no agent is left without one.
  Each is a real-but-minimal skill: YAML frontmatter (`name`, `description`) + a Markdown
  body seeded from that agent's existing `*_prompt.py` `SYSTEM` text (so day-one behavior
  is unchanged and the bodies can be expanded later), in exactly the shape `scan_skills()`
  parses. One skill per agent:
  - `skills/source-extraction/SKILL.md` (extraction) — what counts as a fact vs. inference;
    keep `source` traceable; how to handle sparse/noisy source text.
  - `skills/profile-synthesis/SKILL.md` (synthesis) — dedupe rules, cross-source conflict
    surfacing (never silently resolve), `raw_source_map` discipline.
  - `skills/job-analysis/SKILL.md` (job_analysis) — how to decompose a posting into
    must-haves vs. nice-to-haves and normalize terminology.
  - `skills/cv-tailoring/SKILL.md` (tailoring) — the current `tailoring_prompt.SYSTEM`
    HARD RULES, lifted verbatim into a skill so they are versioned and testable.
  - `skills/anti-fabrication/SKILL.md` (validation) — the validation cross-check reasoning
    (source-map + similarity thresholds); also composed into the tailoring node's prompt.

  Net mapping is 1:1 agent→primary skill (tailoring additionally composes
  `anti-fabrication`), so all five agents have a starter skill to begin from.
- **`src/agents/skills.py`** — thin project adapter over `fund_models.skills`:
  `resolve_skill(name) -> str` returns a SKILL.md body (frontmatter stripped) for a
  given node, cached; `skills_catalog() -> str` returns the frontmatter-only summary
  (`AgentBase.get_skills_context` format) for discovery. `SKILLS_DIR` resolves from
  config (default `./skills`), so a missing dir degrades gracefully to today's
  behavior (empty context → prompts unchanged).
- **Wire into the structured-output nodes** — these are single-shot
  `with_structured_output` calls, not tool-calling loops, so skills are resolved
  **deterministically by node** rather than via the `load_skill` tool: each prompt
  module gains a `{skill}` slot and the node prepends `resolve_skill("<node-skill>")`
  to its system prompt. `tailoring_prompt.SYSTEM` becomes a thin wrapper that embeds
  the `cv-tailoring` + `anti-fabrication` skills, keeping the prior text as the skill
  body so behavior is unchanged when the skill is present.
- **Migrate the already-implemented Phase 1 agents** — this is a **retrofit of existing
  code**, not new agents. Every Phase 1 node currently carries its reasoning inline in a
  `src/chains/prompts/*_prompt.py` module; each is migrated to source that reasoning from
  its SKILL.md, one node per skill:
  - `src/agents/extraction.py` + `extraction_prompt.py` → `source-extraction`
  - `src/agents/synthesis.py` + `synthesis_prompt.py` → `profile-synthesis`
  - `src/agents/job_analysis.py` + `job_analysis_prompt.py` → `job-analysis`
  - `src/agents/tailoring.py` + `tailoring_prompt.py` → `cv-tailoring` (+ `anti-fabrication`)
  - `src/agents/validation.py` + `validation_prompt.py` → `anti-fabrication`

  Migration is behavior-preserving: the reasoning text moves verbatim from each
  `*_prompt.py` `SYSTEM` string into its SKILL.md body, and the prompt module keeps only
  the structural scaffolding (`{skill}` slot + the `USER` template). Because
  `resolve_skill()` degrades gracefully, a node whose skill is missing falls back to
  today's behavior — so the migration can land node-by-node without breaking the suite.
- **Config/env** — add `SKILLS_DIR=./skills` to `src/config.py` and `.env.example`;
  document that skills are prompt content, not secrets, and ship in the image.

**Tests:** `tests/unit/test_skills.py` — `scan_skills()` finds all shipped SKILL.md
and returns name/description/path; `resolve_skill()` strips frontmatter and raises on
unknown name; `skills_catalog()` lists every skill. Extended tests for every migrated
node — `test_extraction.py`, `test_synthesis.py`, `test_job_analysis.py`,
`test_tailoring.py`, `test_validation.py` — assert the resolved skill body appears in the
system prompt passed to the mocked LLM, and that a missing `SKILLS_DIR` leaves the call
working (graceful degradation, proving behavior-preservation). No new LLM behavior to
integration-test — the
subset/no-fabrication invariants in the Phase 1 suites still gate correctness.

**Verification:** `pytest tests/unit/test_skills.py -v` green; run `python -m
fund_models.skills skills/` and confirm all five skills print with descriptions;
`POST /tailor` with skills present produces the same no-fabrication behavior as before,
and temporarily unsetting `SKILLS_DIR` still succeeds.

#### Phase 1.c — Caller-directed `profile_id` on `POST /ingest`

An enhancement to Phase 1 that lets a caller choose which profile an ingest
writes to, instead of always minting a fresh `profile_id`. Previously `/ingest`
passed only `{run_id, sources}` into the ingestion graph, so `store_profile`
always called `save_profile(profile, None)` → a new random `profile_id` every
run; re-ingesting updated sources into an existing profile was impossible via the
API even though the storage layer already supported it.

- **New optional `profile_id` form field** on `POST /ingest` (`Form(default=None)`).
  Reuses existing plumbing — `IngestionState.profile_id` and
  `profile_store.save_profile(profile, profile_id)` versioning already existed;
  the route just never populated the field.
- **Validation** — `profile_id` becomes a directory name under
  `data/profiles/`, so `_validate_profile_id()` restricts it to
  `[A-Za-z0-9_-]{1,64}` (no path separators/traversal) and returns **400** on bad
  input, validated **before** any raw inputs are archived.
- **Threaded only when provided** — the id is added to the graph `stream_input`
  solely when supplied, so the omit-it default (mint a fresh id) is unchanged.
- **Behavior** — existing id → a **new version** is appended (`v{n+1}`); unknown
  id → created at **v1**; omitted → server mints a fresh id. `profile_id` remains
  **distinct from `run_id`** (an evolving profile vs. one ingest execution); the
  two are cross-referenced via `run_store` manifest/output linkage, not equal.
- **Docs** — `API-REFERENCE.md` (new request field + 400 case),
  `PRODUCT-GUIDE.md` (re-ingest-into-existing-profile flow).

**Tests:** extended `tests/unit/test_api.py` — `FakeIngestionGraph` records its
input state; `test_ingest_threads_caller_profile_id_into_graph` (id reaches
`stream_input`, response echoes it + version), `test_ingest_without_profile_id_lets_store_mint_one`
(key absent when omitted → default mint), `test_ingest_rejects_unsafe_profile_id`
(`../../etc/passwd` → 400).

**Verification:** `pytest tests/unit/test_api.py -v` green; `POST /ingest` with a
`profile_id` for an existing profile returns that id with an incremented
`version` and writes `data/profiles/{profile_id}/v{n+1}.json`; omitting it still
mints a fresh id; an invalid id returns 400.

#### Phase 1.d — Merge previous ingests into a new profile version (`POST /merge`)

An enhancement to Phase 1. Every `/ingest` run is last-write-wins: re-ingesting
to the same `profile_id` (Phase 1.c) produces a version built **only** from that
run's sources, because `synthesize_profile` sees only the current run's
extractions and never the prior stored profile. This phase adds an explicit
**merge** that combines the stored outputs of two or more prior runs into one new
profile version, preserving cross-source dedupe and conflict surfacing. Merge
operates over the **synthesized snapshots** each run already wrote
(`data/output/{run_id}/output.json`) — no CV re-parse and no per-source Haiku
re-extraction.

- **New `POST /merge` endpoint** — JSON body: `run_ids: list[str]` (≥ 2) +
  optional `profile_id` (target). `profile_id` is validated with the Phase 1.c
  `_validate_profile_id` (existing id → new version, omitted → fresh id). Returns
  the new merge `run_id`, `profile_id`, `version`, and the merged `CareerProfile`
  (incl. `conflicts`). Unknown / snapshot-less `run_id` → 404; fewer than 2
  ids → 400; invalid `profile_id` → 400.
- **Load stored snapshots** — a new `run_store.load_output(run_id) ->
  CareerProfile` reads each run's `data/output/{run_id}/output.json` (mirrors the
  existing `save_output`). The route collects the list of prior profiles to merge.
- **Reuse synthesis for the actual merge** — a `merge_profiles(profiles: list[
  CareerProfile]) -> CareerProfile` node (in `src/agents/synthesis.py` or a new
  `src/agents/merge.py`) reuses `SYNTHESIS_MODEL` + the `profile-synthesis` skill
  (one Sonnet call, structured output `CareerProfile`) to dedupe entries
  describing the same job/project across the input profiles and **surface**
  (never silently resolve) cross-profile disagreements, unioning each input
  profile's existing `conflicts`. `raw_source_map` is rebuilt deterministically
  via the existing `synthesis.build_raw_source_map`, and every entry keeps its
  original `source`, so claim→source traceability survives the merge. *(A purely
  deterministic list-union is rejected: it would duplicate the same job across
  sources and drop conflict surfacing — the core anti-fabrication guarantee. A
  merge USER-prompt variant, e.g. `synthesis_prompt.MERGE_USER`, frames the input
  as "already-synthesized profiles to merge" rather than per-source extractions.)*
- **New merge run for provenance** — the merge is itself assigned a fresh
  `run_id`; the merged profile is written to `data/output/{run_new}/output.json`
  (via existing `run_store.save_output`) and its `manifest.json` records
  `merged_from: [run_ids]` (new optional field on `run_store.write_manifest`) and
  links to the produced `profile_id`/`version` via existing `link_profile`. No
  raw source files are archived under `sources/{run_new}/` — a merge run's inputs
  are other runs, referenced by id.
- **Storage** — the merged profile is stored as a new version of the target
  `profile_id` via `profile_store.save_profile` (reusing Phase 1.c semantics).
- **Config/env** — none.

**Tests:** `tests/unit/test_merge.py` — `merge_profiles` with a mocked LLM dedupes
overlapping experiences and surfaces a cross-profile date conflict; `raw_source_map`
is rebuilt and entry `source` fields are preserved; unioned input `conflicts`
carried forward. Extended `test_api.py` — `POST /merge` over two seeded run
outputs returns a new version and the merged profile; unknown `run_id` → 404;
`< 2` ids → 400; invalid `profile_id` → 400 (Phase 1.c path). Extended
`test_run_store.py` — `load_output` roundtrip and `merged_from` in the merge
manifest. No new LLM behavior to integration-test beyond the existing
synthesis/no-fabrication invariants.

**Verification:** seed two runs (`POST /ingest` a CV → r1; `POST /ingest` a GitHub
username → r2), then `POST /merge {run_ids:[r1,r2], profile_id: alice}` →
`GET /profile/alice` latest is the union of both with any conflicting dates in
`conflicts`; `data/output/{run_new}/output.json` exists and its `manifest.json`
carries `merged_from: [r1, r2]` linked to `alice`/`v{n}`.

#### Phase 1.e — Null-tolerant extraction schema + item-level salvage — **implemented 2026-07-21**

A bug fix to Phase 1. A real `POST /ingest` (two CVs + `github_username=thomas-choi`)
failed outright with **HTTP 500** and:

```
{"detail":"ingestion failed: 3 validation errors for SourceExtraction
projects.11.description
  Input should be a valid string [type=string_type, input_value=None, input_type=NoneType]
projects.13.description  ... (same)
projects.27.description  ... (same)"}
```

**Root cause — the skill and the schema contradict each other.**
`skills/source-extraction/SKILL.md` line 12 instructs the model: *"If a field is absent
from the document, leave it empty/null"*, and the "Sparse or noisy sources" section
repeats it. But `Project.description` in `src/models/schemas.py` is a required,
non-nullable `str`. GitHub repos with no repo description contribute no `Description:`
line to the source text (`src/tools/github_client.py:59-60`), so the extractor correctly
emitted `"description": null` for those repos — and Pydantic rejected the **entire**
`SourceExtraction`. Indices 11/13/27 are repo positions within the single
`github:thomas-choi` source document (`MAX_REPOS = 30`). The failure was fatal: three
missing repo descriptions discarded a 30-repo GitHub extraction **and** both parsed CVs,
because `extract_source` (`src/agents/ingestion_graph.py:33-36`) has no error handling.
The same latent trap exists on every other required `str` in the extraction-facing
models. The uploaded CVs *were* archived under `data/sources/{run_id}/` before the
failure (Phase 1.a), so only the LLM work was lost.

**Granularity note (drives the design below):** one `github_username` produces exactly
**one** `SourceDocument` containing all repos (`src/api/routes.py:122-123`), and
`extract_source` iterates over *source documents*. A skip at that level would therefore
throw away all 30 repos to survive 3 bad ones. Per-repo (per-item) resilience must live
**inside `extract_one`**, at the point where the LLM payload is validated — which is the
only place individual projects exist. The source-level guard is kept only as a coarse
last-resort net for hard failures.

- **Fix 1 — schema tolerates `null` (removes the root cause).** In
  `src/models/schemas.py`, add a module-level `mode="before"` validator helper
  (`_blank_if_none` → `"" if v is None else v`, and `_empty_if_none` → `[] if v is None
  else v`) and apply it to the fields the extractor can legitimately null out:
  - `Project.description` (also gains a `= ""` default), `Project.name`, `Project.source`
  - `Experience.company`, `Experience.title`, `Experience.source`
  - `Skill.name`, `Skill.category`
  - list fields via `_empty_if_none`: `Experience.bullets`, `Project.technologies`,
    and the `JobRequirements` list fields (same failure mode on a sparse posting).

  Models that are **not** LLM-extraction targets (`TailoredCV`, `ValidationFlag`,
  `ValidationResult`, `CoverLetter`) stay strict — a `null` there is a real bug and must
  still raise. `SKILL.md` is **not** changed: "never invent, leave it empty/null" is the
  anti-fabrication guarantee, and the schema is what should yield.
- **Fix 1b — `raw_source_map` collision ripple.** `synthesis.build_raw_source_map`
  (`src/agents/synthesis.py:20-31`) keys projects by `proj.description`. Once descriptions
  can be `""`, every description-less project collides on the single `""` key and injects
  a meaningless entry into the map that the anti-fabrication gate reads
  (`src/agents/validation.py:73`). Skip falsy `description`/`bullet`/`skill.name` values
  when building the map.
- **Fix 2 — item-level salvage in `extract_one`** (`src/agents/extraction.py`). Switch to
  `with_structured_output(SourceExtraction, include_raw=True)`, which returns
  `{"parsed", "raw", "parsing_error"}` and **surfaces** the `ValidationError` instead of
  raising it:
  - Happy path (`parsing_error is None`) → use `parsed`; behavior identical to today,
    including the existing `source`-overwrite loop and debug logging.
  - Failure path → read the raw tool-call args (`raw.tool_calls[0]["args"]`) and rebuild
    the `SourceExtraction` field by field, validating `experiences` / `projects` / `skills`
    **one element at a time** (`Model.model_validate(item)`), dropping the ones that fail
    and logging each at `WARNING` with its list index, its `name` if present, and the
    pydantic message. For this bug that keeps 27 repos and drops 3.
  - If salvage recovers nothing usable (no tool call, unparseable args, or every item
    rejected) → **re-raise the original error**. A silently empty profile is worse than a
    500.
  - The strict `SourceExtraction` stays the tool schema handed to the model (it is what
    steers the output); the lenient handling exists only on the error path. With Fix 1 in
    place this path should rarely fire — it is defense-in-depth for the next malformed
    field, not the primary remedy.
- **Fix 2b — coarse source-level net** in `extract_source`
  (`src/agents/ingestion_graph.py:33-36`): wrap the per-source call so a hard failure
  (provider error, no parseable response at all) logs the source id and continues, but
  raise if **no** source survives. Explicitly *not* the mechanism that saves the repos —
  losing a source here still means losing the whole GitHub profile.
- **Config/env** — none. **API contract** — unchanged (no new fields, no new status codes);
  `/ingest` simply stops 500-ing on this input.

**Tests:**
- `tests/conftest.py` — `FakeLLM.with_structured_output(schema, include_raw=False)` must
  accept the kwarg and, when true, return the `{"parsed", "raw", "parsing_error"}`
  envelope (with a `raw` stub exposing `tool_calls`); existing callers unchanged.
- `tests/unit/test_schemas.py` (new or extended) — `Project(description=None)` → `""`;
  `Experience(bullets=None)` → `[]`; `TailoredCV` still rejects `null` (strictness kept
  where it matters).
- `tests/unit/test_extraction.py` — regression test reproducing the exact payload (a
  30-project list with `description=None` at indices 11/13/27 → all 30 survive with
  `""`); a genuinely malformed project is dropped while its siblings survive and the drop
  is logged; total salvage failure re-raises; the existing source-overwrite and
  skill-in-prompt tests still pass.
- `tests/unit/test_synthesis.py` — two description-less projects do not collide in
  `raw_source_map`, and no `""` key is emitted.
- `tests/unit/test_graphs.py` — one dead source is skipped and the run completes with the
  remaining sources; all-dead raises.

**Verification:** `pytest tests/unit/ -v` green (no regressions across Phases 1–1.d);
then re-run the failing command against the container —

```bash
curl -F "cv=@ThomasChoi-Trading-ML-20230910.pdf" -F "cv=@Thomas+Choi+Trading-20240708.docx" \
     -F "github_username=thomas-choi" -F "profile_id=thomas-main" -F "job_id=merge001" \
     192.168.0.212:8000/ingest
```

→ HTTP 200 with a `CareerProfile` whose projects include the previously-rejected repos
(empty `description`, other fields intact), `data/output/{run_id}/output.json` written,
and no `string_type` error in the logs.

**Branch:** `fix/extraction-null-tolerance`. **Docs to update on completion:**
`HISTORY.md` (new top entry), `TECHNICAL-DESIGN.md` §4 (nullable-field contract +
two-tier extraction resilience), `API-REFERENCE.md` (one-line note: no API change),
`OPERATIONS.md` (one-line note: no setup change), `PRODUCT-GUIDE.md` (partial extraction
is logged and salvaged, not fatal).

#### Phase 1.f — GitHub ingestion beyond personally-owned repos — **implemented 2026-07-21**

An enhancement to Phase 1. `fetch_github_profile` calls
`/users/{u}/repos?type=owner` (`src/tools/github_client.py:44`), which **by
definition** returns only repos under the personal username. Work done inside
organizations, and contributions to other people's repos, are invisible to the
profile — for many candidates that is the majority of their real engineering
output. Verified against the live API with a representative user: `type=owner`
→ 31 repos, **0** external; the same user has 3 public org/collaborator repos and
1052 merged PRs across repos such as `pallets/flask`, `pypa/pip`,
`readthedocs/readthedocs.org`.

**API surface (each endpoint below was verified live before being planned):**

| Source | Endpoint | Token | Notes |
|---|---|---|---|
| Org / collaborator repos | `GET /users/{u}/repos?type=all` | no | owner + member; `type=member` alone returns just the external ones |
| Public org memberships | `GET /users/{u}/orgs` | no | only orgs the user made public |
| Contributions to others' repos | `GET /search/issues?q=author:{u}+type:pr+is:merged` | no | per-PR `repository_url`, title, state; 10 req/min unauth, 30 auth |
| Contributed-repo list (richer) | GraphQL `user.repositoriesContributedTo(includeUserRepositories:false)` | **yes** | all-time, not owned by user, with description/language/stars in one call |
| Per-repo commit counts | GraphQL `contributionsCollection.commitContributionsByRepository` | **yes** | ⚠️ **defaults to the last 12 months** — must loop `from`/`to` per year for full history |
| Private / internal repos | `GET /user/repos?affiliation=...` | user's own token | **out of scope** — unreachable for a third-party username; see below |

`GET /search/commits?q=author:{u}` is **rejected**: it counts forks and mirrors
and reported 57,068 commits for a user with 778 real commit contributions.

- **Fix 1 — include org / collaborator repos.** `type=owner` → `type=all`, and
  partition the result by `repo["owner"]["login"]`: repos owned by `username` are
  *owned*, the rest are *member* repos. Fetch `/users/{u}/orgs` so the source text
  can name the organizations. Forks stay excluded (a fork is not evidence; the
  merged PR it produced is — see Fix 2).
- **Fix 2 — contributions to repos the user neither owns nor is a member of.**
  Two interchangeable providers behind one internal function, selected by token
  presence so the no-token path still improves:
  - **With `GITHUB_TOKEN`** — one GraphQL call for `repositoriesContributedTo`
    (repo name, description, primary language, stars) plus
    `commitContributionsByRepository` looped over the user's active years for
    per-repo commit counts.
  - **Without a token** — REST merged-PR search, aggregated per repo into a count
    plus the first *N* PR titles.

  Either way each external repo yields a **contribution scope** — "6 merged PRs;
  41 commits" — and the PR titles, which are the actual resume-grade evidence.
- **Fix 3 — attribution discipline (anti-fabrication).** Today every repo is
  rendered under a bare `## Repository: <name>` heading with its description and
  README excerpt (`src/tools/github_client.py:56-85`). Applied unchanged to
  `pallets/flask`, that reads as *the user's project* and invites synthesis to
  credit them with the whole framework — the exact failure
  `skills/anti-fabrication/SKILL.md` exists to prevent. Therefore the source
  document is restructured into three explicitly-labelled sections:
  `## Owned repositories`, `## Organization repositories (member of <org>)`, and
  `## Contributions to external repositories (not owned by the user)`. Repos in
  the third section carry their contribution scope and **no README excerpt** —
  a README describes the project, not the contribution. Correspondingly
  `skills/source-extraction/SKILL.md` gains one rule: a contribution to a repo the
  user does not own is evidence of *that contribution*, never of authorship or
  ownership of the project.
- **Fix 4 — call and token budget.** Every repo currently costs 2 extra calls
  (languages + readme) and up to 1500 README chars, and `MAX_REPOS = 30` truncates
  by `updated` — which would silently drop the most interesting external repos.
  So: README + languages are fetched **only** for owned and org-member repos;
  external repos are sorted by contribution count (not `updated`) and capped by a
  separate `MAX_EXTERNAL_REPOS`; the merged-PR search is paged once, not
  exhaustively. A 403/429 from the search endpoint degrades to "owned + org repos
  only" with a `WARNING`, never a failed ingest.
- **Private / internal org repos — explicitly out of scope, and documented as
  such.** They are reachable only when `GITHUB_TOKEN` is *the user's own* token
  carrying `repo` scope with org SSO authorized, via
  `GET /user/repos?affiliation=owner,collaborator,organization_member`. Since
  `/ingest` takes a bare `github_username` and the server token is an operator
  credential, silently returning private repos for whichever username is typed
  would be wrong. Accepting a caller-supplied token is deferred (it is a
  credential-handling decision, not a GitHub-API one).
- **Config/env** — `GITHUB_INCLUDE_CONTRIBUTIONS=true` (kill switch for the extra
  search/GraphQL calls) and `GITHUB_MAX_EXTERNAL_REPOS=15`, added to
  `src/config.py` and `.env.example`. `GITHUB_TOKEN` stays optional but its
  description changes: it now unlocks richer contribution data, not just rate
  limits. **API contract** — unchanged; `/ingest` takes the same
  `github_username` and returns the same `CareerProfile`, with more of it
  populated.

**Tests:** `tests/unit/test_github_client.py` — extend the existing
`httpx.MockTransport` handler (no network) to serve `type=all` with a mixed
owned/org payload, `/users/{u}/orgs`, and a `/search/issues` page; assert the
three section headings appear, that an org repo is attributed to its org, that an
external repo shows its merged-PR count and titles but **no README excerpt**,
that forks are still excluded, and that a 403 on search still yields a document
containing the owned repos. A token-present case stubs the GraphQL POST and
asserts the contributed-repo list is used instead of the search path.
`tests/unit/test_extraction.py` — a source document containing an external-repo
section does not produce a `Project` attributed to the user (the anti-fabrication
invariant, mocked LLM asserting the prompt carries the not-owned framing).

**Verification:** `pytest tests/unit/ -v` green (no regressions across
Phases 1–1.e); then, against the container, `POST /ingest` with
`github_username=thomas-choi` → the returned `CareerProfile` includes work from
repos outside `thomas-choi/*`, each external item's `source` traceable to
`github:thomas-choi`, and `data/sources/{run_id}/github/github.json` showing the
three labelled sections. Re-run with `GITHUB_INCLUDE_CONTRIBUTIONS=false` → the
document degrades to owned + org repos only. Unset `GITHUB_TOKEN` → the REST
search path produces a comparable (smaller) contribution list.

**Branch:** `feat/github-org-contributions`. **Docs to update on completion:**
`HISTORY.md` (new top entry), `TECHNICAL-DESIGN.md` §3 (GitHub source coverage +
the owned/member/contributed attribution contract), `OPERATIONS.md` (two new env
vars; `GITHUB_TOKEN` rationale; GitHub rate-limit note), `PRODUCT-GUIDE.md` (org
and open-source contributions now appear in the profile; private repos do not and
why), `API-REFERENCE.md` (one-line note: no API change).

#### Phase 1.g — Private org membership + "access is not contribution" — **implemented 2026-07-21**

Follow-up to 1.f, prompted by reviewing its live output. Two GitHub behaviours
invalidated assumptions 1.f was built on:

1. **`GET /users/{u}/orgs` lists public memberships only, and private is the
   default.** It returned `[]` for `thomas-choi` while he belongs to 5 orgs. The
   consequence was not just a gap: 14 repos belonging to his *own companies* had
   been labelled "external contributions **not owned by the user**", inverting
   the attribution 1.f existed to protect. 1.f's premise that `GITHUB_TOKEN` is a
   third-party operator credential was also false — `GET /user` shows the
   configured token is the ingested user's own.
2. **`affiliation=collaborator` returns every repo the user was ever invited
   to** — 88 for this account, with commits in exactly 1.

**Implemented:** a self-token path (`GET /user` identity check → `/user/orgs` +
paged `/user/repos?affiliation=owner,organization_member,collaborator`), gated so
a token belonging to anyone but the ingested username never reaches a viewer
endpoint; a commit probe (`GET /repos/{full}/commits?author={u}`, evidence-first,
budget-bounded) that every non-owned repo must pass; and an org cap that seeds
one repo per organization before filling by recency, so an old employer is not
evicted by a busy current one. GraphQL `contributionsCollection` was evaluated as
the sole contribution filter and rejected (8 of 26 true positives — it counts
default-branch commits under a matching account email only).

**Config/env:** `GITHUB_INCLUDE_PRIVATE=true`, `GITHUB_MAX_CONTRIBUTION_PROBES=150`,
`GITHUB_MAX_ORG_REPOS=20`. **API contract** — unchanged.

**Result:** 5 org memberships found (was 0); 20 org repos across 5 employers from
66 candidates (was 2); 18 private repos included; external section legitimately
empty for this user.

## Phase 2 — LinkedIn export ingestion (design doc §12 step 5) — **implemented 2026-07-21**

1. `src/tools/linkedin_export.py` — accept the official LinkedIn data-export ZIP (or individual CSVs): parse Positions, Education, Skills, Certifications, Recommendations into `SourceDocument`s with `structured_fields`. **No scraping** (ToS), exactly per design doc §3.
2. Extend `POST /ingest` to accept a `linkedin_export` ZIP upload; register the source in the ingestion graph (extraction prompt gains a LinkedIn variant that trusts `structured_fields` over raw text).
3. Synthesis already handles multi-source dedupe/conflicts — extend unit tests to cover LinkedIn-vs-CV conflicts (same job, different dates → appears in `conflicts`).

**Tests:** `tests/unit/test_linkedin_export.py` (fixture ZIP/CSVs), extended `test_synthesis.py` conflict cases, extended `test_api.py` upload case.

**As implemented:** one `SourceDocument` per upload (`source_type="linkedin"`,
`id="linkedin:<filename>"`) carrying the exported rows in `structured_fields`
*and* a deterministic Markdown rendering in `raw_text`; both are sent to the
extractor, with the records declared authoritative via a `{structured}` slot
that stays empty for prose sources (so their prompt is byte-identical to
Phase 1 — no graph branch was needed). Two real-export quirks the design had
not anticipated: section files are matched on a **normalized** stem
(`Recommendations_Received.csv` ≡ `Recommendations Received.csv`), and the
header row is **located by its columns**, because LinkedIn prefixes several
CSVs with a free-text `Notes:` preamble. An export with no recognized section,
a corrupt ZIP, or a non-`.zip`/`.csv` upload → **400**, after the raw upload is
archived under `data/sources/{run_id}/linkedin/` (archive-before-parse, as for
CVs). `skills/source-extraction/SKILL.md` gained the two attribution rules the
export makes necessary: profile skills are self-asserted (never promoted into
achievements) and recommendations are third-party statements (never restated as
the person's own claims). No new env vars, no new dependencies. Suite: 128
unit tests green.

## Phase 3 — Document Agent: rendering + cover letter (design doc §9, §1)

1. `src/tools/docx_renderer.py` + `src/agents/document.py` — pure rendering (no LLM) of `TailoredCV` → `.docx` from a bundled template (name/contact header, summary, experiences, projects, skills); PDF via `libreoffice --headless` in the Docker image (add to Dockerfile).
2. Add `render_document` node to the tailoring graph after `validate_cv` (skipped when validation flags exist and caller hasn't approved).
3. **Cover letter (optional output per design doc §1):** `generate_cover_letter` prompt in tailoring agent, same no-fabrication constraints, returns `CoverLetter` schema; rendered by the same document agent.
4. API: `POST /tailor` gains `render: bool` + `cover_letter: bool` flags; new `GET /document/{tailor_id}` returns the .docx/.pdf file.

**Tests:** `tests/unit/test_docx_renderer.py` (render fixture `TailoredCV`, re-open with python-docx and assert content/ordering), `test_document_agent.py` (skip-on-flags behavior), cover-letter prompt unit test (mocked LLM), extended API tests. PDF conversion covered by an integration test (needs LibreOffice, runs in Docker).

## Phase 4 — Review UI + human-in-the-loop (design doc §10 frontend, §8 interrupt, §11 guardrails)

1. `frontend/` — React + Vite + TanStack Query. Three-panel flow per design doc §10:
   - **Sources panel:** upload CV/LinkedIn ZIP, GitHub username, free text; live SSE progress.
   - **Profile panel:** review/edit `CareerProfile`, resolve `conflicts` explicitly (writes new version via `PUT /profile/{id}`).
   - **Tailor panel:** paste job post → side-by-side diff of original vs. tailored bullets, `needs_review` flags highlighted, approve/reject each flagged item, then trigger render + download.
2. **Human review checkpoint server-side:** add a `MemorySaver` checkpointer to the tailoring graph and an `interrupt()` between `validate_cv` and `render_document`; new endpoints `GET /tailor/{tailor_id}/review` (pending flags) and `POST /tailor/{tailor_id}/resume` (approvals in → graph resumes to render). This completes design doc §11: no CV is rendered without the person seeing flagged items.
3. Serve the built frontend from FastAPI (`StaticFiles`) so it stays one container; multi-stage Dockerfile (node build stage → python runtime stage).
4. **Adopt `fund_models/agent_base.py` for the tool-calling review node (deferred from Phase 1.b):** the human-in-the-loop resume step is the first genuinely *agentic* (tool-calling) node, so it is implemented as an `AgentBase`/`DeepAgentMixin` subclass. This is where the rest of the FUND skill machinery finally earns its place — `AgentBase._load_skills`/`get_skills_context` load the same `skills/` directory, and `make_load_skill_tool` registers the runtime `load_skill_from_fs` tool so the node can pull a full skill body (e.g. `anti-fabrication`) on demand during the review loop, rather than the deterministic per-node resolution used by the Phase 1.b structured nodes. The Phase 1 nodes stay as functional `make_llm` nodes; only the new agentic node subclasses `AgentBase`.

**Tests:** frontend unit tests with vitest (panel state, diff view, flag approval flow); backend `test_review_flow.py` — interrupt fires on flags, resume renders, no-flag runs skip straight to render; API tests for the two new endpoints; `test_review_agent.py` — the `AgentBase` review node loads skills and its `load_skill_from_fs` tool returns a known skill body.

---

## Docs sync (every phase, mandatory per CLAUDE.md)

- `TECHNICAL-DESIGN.md` — "Implementation notes" section per phase (two-graph split, JSON storage, interrupt design in Phase 4) + Mermaid diagrams of implemented graphs.
- `OPERATIONS.md` (new in Phase 1) — setup, env-vars table, docker/test commands; updated when Phase 3 adds LibreOffice and Phase 4 adds the frontend build.
- `API-REFERENCE.md` (new in Phase 1) — every endpoint added/changed per phase.
- `PRODUCT-GUIDE.md` (new in Phase 1) — user flows and current limitations, updated as phases remove limitations.
- `HISTORY.md` (new in Phase 1) — one table entry per change, newest first, required format.
- Project `CLAUDE.md` — replace "no code exists" section with real run/test commands in Phase 1.

## Test policy (per mandatory rules, applies to every phase)

1. **All existing tests pass** — each phase must leave the previous phases' suites green; no regressions.
2. **Removed tests announced** — none anticipated; if a phase obsoletes a test (e.g. Phase 4 replaces client-side review tests), it is named and the reason recorded in `HISTORY.md` before removal.
3. **New tests announced** — listed per phase above; every new code path ships with unit coverage, LLM calls mocked in unit tests, real-API paths under `@pytest.mark.integration`.
4. **Tests documented** — added/removed test files recorded in `HISTORY.md` and commands in `OPERATIONS.md`/`CLAUDE.md`.

## Verification (end of each phase)

- `pytest tests/unit/ -v` green; `pytest -m integration` with real keys as final gate.
- **Phase 1:** `docker compose up --build`; `curl -F cv=@sample.docx -F github_username=<user> localhost:8000/ingest` → inspect `CareerProfile` + `data/profiles/`; `POST /tailor` → confirm output only contains profile-sourced facts and an intentionally injected fake skill gets flagged.
- **Phase 1 (run tracking):** after an `/ingest` call, confirm the returned `run_id` has `data/sources/{run_id}/` holding the raw CV, `github.json`, `linkedin-summary.txt`, and `manifest.json`, and that `data/output/{run_id}/output.json` matches the returned profile.
- **Phase 2:** ingest a real LinkedIn export ZIP alongside a CV → conflicting dates appear in `conflicts`, not silently merged.
- **Phase 3:** tailor with `render=true` → open the .docx/.pdf, verify structure matches `TailoredCV` ordering.
- **Phase 4:** full browser flow from the Windows host (server on `0.0.0.0`): upload → edit profile → tailor → approve a flagged item → download rendered CV; verify a flagged run cannot render without approval.
