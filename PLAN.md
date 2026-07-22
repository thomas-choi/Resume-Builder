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

## Phase 3 — Document Agent: rendering + cover letter (design doc §9, §1) — **implemented 2026-07-21**

1. `src/tools/docx_renderer.py` + `src/agents/document.py` — pure rendering (no LLM) of `TailoredCV` → `.docx` from a bundled template (name/contact header, summary, experiences, projects, skills); PDF via `libreoffice --headless` in the Docker image (add to Dockerfile).
2. Add `render_document` node to the tailoring graph after `validate_cv` (skipped when validation flags exist and caller hasn't approved).
3. **Cover letter (optional output per design doc §1):** `generate_cover_letter` prompt in tailoring agent, same no-fabrication constraints, returns `CoverLetter` schema; rendered by the same document agent.
4. API: `POST /tailor` gains `render: bool` + `cover_letter: bool` flags; new `GET /document/{tailor_id}` returns the .docx/.pdf file.

**Tests:** `tests/unit/test_docx_renderer.py` (render fixture `TailoredCV`, re-open with python-docx and assert content/ordering), `test_document_agent.py` (skip-on-flags behavior), cover-letter prompt unit test (mocked LLM), extended API tests. PDF conversion covered by an integration test (needs LibreOffice, runs in Docker).

**As implemented:** the render decision and the layout are separate modules —
`src/agents/document.py` owns `skip_reason()` (the gate) and
`src/tools/docx_renderer.py` only draws, so neither can be tested through the
other. Three things the plan had not settled:

- **Approval needed a request field.** "Skipped when validation flags exist and
  the caller hasn't approved" has no meaning without a way to approve, so
  `POST /tailor` gained `approve_flagged` alongside `render`/`cover_letter` —
  otherwise a flagged run could never render at all in Phase 3.
- **`TailoredCV` has no name or contact**, so the renderer takes them from the
  `CareerProfile` (`render_cv(cv, path, name, contact)`); the schema was left
  alone rather than duplicating identity onto every tailored CV.
- **"A bundled template" became an optional one.** `DOCX_TEMPLATE` points at a
  base `.docx` supplying styles/letterhead and the renderer always *appends*
  content, so no placeholder-substitution engine exists; unset, python-docx's
  default template is used, and a template lacking `Heading 1`/`List Bullet`
  degrades to bold/plain paragraphs instead of raising.

Also added beyond the plan: `src/utils/document_store.py` (third store, keyed by
`tailor_id`, with filenames fixed per kind/format so a download cannot escape
its directory, plus a `tailor.json` provenance copy) and a `cover-letter`
SKILL.md composed with `anti-fabrication`, per the Phase 1.b pattern. PDF
conversion runs LibreOffice with a throwaway `-env:UserInstallation` profile
(HOME is not reliably writable in a container) and degrades to docx-only on a
missing/failing binary. Suite: 166 unit tests green; the PDF integration test
passes inside the built image.

## Phase 4 — Review UI + human-in-the-loop (design doc §10 frontend, §8 interrupt, §11 guardrails) — **implemented 2026-07-21**

1. `frontend/` — React + Vite + TanStack Query. Three-panel flow per design doc §10:
   - **Sources panel:** upload CV/LinkedIn ZIP, GitHub username, free text; live SSE progress.
   - **Profile panel:** review/edit `CareerProfile`, resolve `conflicts` explicitly (writes new version via `PUT /profile/{id}`).
   - **Tailor panel:** paste job post → side-by-side diff of original vs. tailored bullets, `needs_review` flags highlighted, approve/reject each flagged item, then trigger render + download.
2. **Human review checkpoint server-side:** add a `MemorySaver` checkpointer to the tailoring graph and an `interrupt()` between `validate_cv` and `render_document`; new endpoints `GET /tailor/{tailor_id}/review` (pending flags) and `POST /tailor/{tailor_id}/resume` (approvals in → graph resumes to render). This completes design doc §11: no CV is rendered without the person seeing flagged items.
3. Serve the built frontend from FastAPI (`StaticFiles`) so it stays one container; multi-stage Dockerfile (node build stage → python runtime stage).
4. **Adopt `fund_models/agent_base.py` for the tool-calling review node (deferred from Phase 1.b):** the human-in-the-loop resume step is the first genuinely *agentic* (tool-calling) node, so it is implemented as an `AgentBase`/`DeepAgentMixin` subclass. This is where the rest of the FUND skill machinery finally earns its place — `AgentBase._load_skills`/`get_skills_context` load the same `skills/` directory, and `make_load_skill_tool` registers the runtime `load_skill_from_fs` tool so the node can pull a full skill body (e.g. `anti-fabrication`) on demand during the review loop, rather than the deterministic per-node resolution used by the Phase 1.b structured nodes. The Phase 1 nodes stay as functional `make_llm` nodes; only the new agentic node subclasses `AgentBase`.

**Tests:** frontend unit tests with vitest (panel state, diff view, flag approval flow); backend `test_review_flow.py` — interrupt fires on flags, resume renders, no-flag runs skip straight to render; API tests for the two new endpoints; `test_review_agent.py` — the `AgentBase` review node loads skills and its `load_skill_from_fs` tool returns a known skill body.

**As implemented:** the plan's four items landed as written; four things it had
not settled:

- **A decision needed a shape.** "Approvals in → graph resumes" says nothing
  about *what* an approval does. Approving each flagged item individually is
  the only version that respects the person: `ReviewDecision.approvals` maps a
  stable `ReviewItem.id` to keep/remove, and **anything not approved is removed
  from the CV** rather than the whole run being lost. An item left unanswered
  counts as not approved — silence cannot be consent for a claim nothing could
  trace.
- **Prose leaks.** Removing a claim from `highlighted_skills` left it asserted
  in the tailored `summary` ("proficient in … Docker") — caught in a live run
  against the container, not by a test. Pruning now also drops summary
  sentences naming a rejected term and falls back to the profile's headline
  when the tailored one does. The underlying hole stays open and is recorded:
  the validation gate never inspects prose at all.
- **The cover letter had to move.** It was written before the review, so a
  rejected bullet could reappear in the letter. Its conditional edge moved from
  `validate_cv` to `human_review`.
- **The review checkpoint is two nodes, not one.** LangGraph re-runs an
  interrupted node from the top when it resumes, so a single `human_review`
  node would re-pay for the reviewer's brief (an LLM call) on every resume.
  `prepare_review` does everything with a cost or a side effect and completes;
  `human_review` holds only the `interrupt()` and the decision handling.
- **`interrupt()` needs somewhere to pause.** A module-level `MemorySaver`
  shared by every compiled graph (`thread_id = tailor_id`), because the resume
  arrives on a later HTTP request. In-process: a restart loses pending resumes
  (`409`), so the `ReviewRequest` is archived to
  `data/documents/{tailor_id}/review.json` before pausing — the record survives
  even when the ability to continue does not.

Also beyond the plan: `Conflict.resolution` (the review UI records *which*
value the person chose, keeping the disagreement), a `.dockerignore`, and
`REVIEW_MODEL` / `REVIEW_AGENT_ENABLED` / `REVIEW_MAX_TOOL_ITERATIONS` /
`FRONTEND_DIR`. `ReviewAgent` overrides `AgentBase.get_llm` onto this project's
`make_llm` — FUND's version sends a `temperature` current Claude models reject,
and `make_llm` is the single mock point the test suite relies on. Suite: 205
unit tests + 33 vitest tests green; the full pause → review → partial approval
→ render path verified against the running container.

---

## Phase 5 — Multi-source UI hardening (findings from the first end-to-end UI run) — **planned**

Found by driving the Phase 4 UI end to end on 2026-07-21 with a real set of
inputs: one `CV.docx`, one `CV.pdf`, one GitHub username, one LinkedIn data
export. Three defects, each diagnosed below with the evidence that proves it —
re-read this before touching code, the root causes are not what the symptoms
suggest.

### Evidence from the failing run (`run_id = ui-mrvl80oa-udexcp`)

`logs/app.log` around the GitHub extraction:

```
extract[github:thomas-choi]: type=github, input 48959 chars, model=deepseek-v4-flash
WARNING structured output failed to validate, attempting item-level salvage: None
ERROR   extract[github:thomas-choi]: no parseable tool-call arguments to salvage from
ERROR   extraction failed for source github:thomas-choi: ... returned no usable output
DEBUG   ** Synthesizing profile from 2 extractions
```

Three sources went in, two extractions came out, and `/ingest` still returned
**HTTP 200** with a success banner in the UI. `data/profiles/thomas-new-1/v1.json`
has 6 experiences, 60 skills and **0 projects**. The same GitHub source extracts
fine on its own (`data/profiles/github0001p/` → 28–50 projects), so this is
output-size fragility, not a hard bug: ~49k chars in, ~50 repos of structured
output back, and the model returned a message with **no tool call at all**
(both `parsed` and `parsing_error` were `None`, so the item-level salvage in
`extraction._salvage` had nothing to work with).

### 5.a — Per-request GitHub token — **implemented 2026-07-21**

**Problem.** `src/tools/github_client.py::_headers` builds auth from the
module-global `config.GITHUB_TOKEN`, read once at import; `_viewer_login` uses
the same global to decide whether the token is a "self-token" (which unlocks
private repos and private org memberships). One process therefore serves
exactly one token — ingesting a second username with *their* token means
editing `.env` and restarting.

1. `src/tools/github_client.py` — add an explicit `token: str | None = None`
   parameter to `fetch_github_profile`, threaded down into `_headers(token)`,
   `_viewer_login(client, token)`, `_graphql(...)` and `_gather_evidence(...)`.
   Resolve as `token or config.GITHUB_TOKEN` at the top of
   `fetch_github_profile` so env-only deployments behave exactly as today. The
   `is_self` determination then happens per request, so user B's token unlocks
   B's private repos without a restart, and a third-party token still never
   reaches the viewer endpoints.
2. `src/api/routes.py::ingest` — add `github_token: str | None = Form(default=None)`
   and pass it to `fetch_github_profile`. The token is a **secret in transit
   only**: it must not appear in `manifest.json`, not in the archived GitHub
   source document, and not in any log line (the existing
   `github[%s]: token viewer=%s` debug logs the resolved login, which is fine).
3. `frontend/src/panels/SourcesPanel.tsx` + `lib/api.ts` — a
   `type="password"` "GitHub token (optional)" field under the username, held
   in component state only (**no localStorage**), appended to the `FormData` as
   `github_token` only when non-empty. Helper text: a token for the username
   being ingested also unlocks their private repos and org memberships; a token
   for anyone else only raises rate limits.

**Tests:** `tests/unit/test_github_client.py` — explicit token overrides
`config.GITHUB_TOKEN`; a token whose viewer login ≠ the ingested username never
calls `/user/repos`. `tests/unit/test_api.py` — the form field reaches
`fetch_github_profile` and appears nowhere in the written manifest.
`frontend/src/__tests__/SourcesPanel.test.tsx` — the field is `type="password"`
and is omitted from the request body when blank.

### 5.b — Multiple CV / export entries — **implemented 2026-07-21**

Two independent defects produce the same symptom (a second file silently
disappears):

1. **UI replaces instead of accumulating.** `SourcesPanel.tsx` does
   `setCvFiles(Array.from(event.target.files ?? []))` on every `change`, so
   picking `CV.docx` and then `CV.pdf` in a second click drops the first. Fix:
   append to the existing array, de-duplicate on `name+size+lastModified`,
   render the staged files as a list with a per-file remove button, and set
   `event.target.value = ""` after reading so re-picking the same file fires
   `change` again. Apply the same fix to the `linkedin_export` input.
2. **Backend filename collision.** `run_store.save_source_file` writes to
   `data/sources/{run_id}/{category}/{filename}`, so two uploads with the same
   name overwrite each other; worse, `routes._load_upload` derives
   `doc.id = f"{source_type}:{filename}"`, so the two sources also collide as
   source ids and corrupt `CareerProfile.raw_source_map` traceability. Fix:
   `save_source_file` de-duplicates on collision (`CV.docx` → `CV-2.docx`,
   `CV-3.docx`, …) and returns the path actually written; `_load_upload` and
   `_load_linkedin_export` derive `doc.id` from `stored.name` rather than the
   uploaded `filename`, so two same-named CVs stay distinct end to end.

The API already accepts `list[UploadFile]` for both `cv` and `linkedin_export`
— no signature change is needed.

**Tests:** `tests/unit/test_run_store.py` — same name twice → two files, two
paths. `tests/unit/test_api.py` — two same-named CVs → two archived files, two
distinct source ids in the manifest. `SourcesPanel.test.tsx` — two successive
picks accumulate; remove drops one; re-picking a removed file re-adds it.

**As implemented (5.a + 5.b):** both landed as written. Three things the plan
had not settled:

- **Reading the pick and clearing the input are order-dependent.** Setting
  `event.target.value = ""` empties `event.target.files`, and a React state
  updater runs *after* the handler returns — so the first version, which read
  `event.target.files` inside the updater, staged nothing on the second pick.
  The pick is read into a local before the clear. The accumulate test caught
  this, not review.
- **`anyio.to_thread.run_sync` takes no keyword arguments**, so the token
  reaches `fetch_github_profile` via `functools.partial`.
- **"Not in the manifest" was worth testing literally.** The token test walks
  every file written under `data/` and asserts the secret is in none of them,
  rather than checking the one file it was expected to leak into.

Also: `save_source_file`'s docstring now states that the returned path is not
always `{category}/{filename}` — a caller deriving an id from the name it passed
in is exactly the bug 5.b fixed. `tests/conftest.py::build_sample_docx` gained a
`name` argument so two uploads can be told apart by content. Suite: 213 unit +
40 vitest green.

### 5.c — GitHub extraction: per-repo isolation and reporting — **implemented 2026-07-22**

**Decision (2026-07-21):** handle this at the *extraction* layer only. Report
each failed repo in the UI by name with a short reason, keep only the good repo
content in the archived GitHub source, and synthesize the profile from the
survivors. **`src/agents/synthesis.py` is explicitly out of scope for this
phase** — it still funnels every project through one LLM call that must re-emit
them all, which is the same output-size fragility one stage later; revisit only
if it demonstrably drops data once extraction is reliable.

1. **Split by repo.** New helper (e.g. `src/tools/github_client.py::split_repo_sections`
   or a small `src/agents/github_chunks.py`) that segments the rendered GitHub
   document on its `### Repository: owner/name` and external-contribution
   boundaries. Each chunk must carry **its tier heading and preamble** (the
   "Owned repositories" / "Organization repositories (… owner)" /
   "Contributions to external repositories" blocks written by
   `fetch_github_profile`), because `skills/source-extraction/SKILL.md` decides
   ownership-vs-contribution attribution from exactly that labelling. A chunk
   that loses its heading will be mis-attributed as authorship.
2. **Batch, then isolate.** In `src/agents/extraction.py`, a `source_type ==
   "github"` document is extracted in batches of `GITHUB_REPOS_PER_EXTRACTION`
   repos (new env var, default 10) instead of one giant call. When a batch
   fails, retry it **one repo at a time** so the failure is attributed to a
   specific repo rather than losing all 50 — this is precisely the failure the
   run above hit. Merge the per-batch `SourceExtraction`s into one (concatenate
   the list fields; `name`/`headline`/`contact` take the first non-empty).
3. **Prune the archive.** Repos that fail even in isolation are dropped from
   the source document. `data/sources/{run_id}/github/github.json` is rewritten
   after extraction to hold **only the repos that reached the profile**, and
   the as-fetched document is preserved next to it as `github.raw.json` with
   its own manifest entry — the audit trail of what GitHub actually returned
   must not be lost to the pruning. Do the rewrite in the node that already
   owns run-store I/O (`store_profile`), fed by new `IngestionState` keys, so
   file writes stay in one place.
4. **Report to the UI.** Add `source_errors: list[dict]` to `IngestionState`
   (`{"source": "github:<user>", "repo": "owner/name", "reason": "<short>"}`),
   returned from `POST /ingest` and published as a `warning` SSE event.
   `SourcesPanel.tsx` renders a "Repos skipped" list with repo name + reason.
   A run that silently lost a whole source must never again render as a clean
   success — that is what made this bug expensive to find.
5. **Real diagnostics.** `extraction._parse_response` / `_salvage` currently log
   the failure as literally `: None`. Log `finish_reason`,
   `response_metadata`/`usage_metadata` and a truncated content preview when
   there is no parseable tool call, so the next occurrence is readable from the
   log alone.

**Tests:** `tests/unit/test_extraction.py` — a multi-repo GitHub document is
split into batches with tier headings intact; a batch failure isolates to the
offending repo and the survivors still extract; merged extraction equals the
single-call result on a document small enough for one batch.
`tests/unit/test_graphs.py` — `source_errors` propagates through the ingestion
graph and the pruned/raw GitHub archives are both written.
`tests/unit/test_api.py` — `source_errors` appears in the `/ingest` response.
`SourcesPanel.test.tsx` — the skipped-repo list renders on a partial-success
response.

**As implemented:** all five items landed as written. What the plan had not
settled:

- **The split had to be byte-exact, not just correct.** `render_repo_document(
  *split_repo_sections(text)) == text` is the property that makes pruning
  trustworthy: a run that drops nothing must leave `github.json` unchanged, so
  any diff in the archive means a repo really was dropped. The spans therefore
  tile the document rather than being re-assembled from parsed parts.
- **A README can forge a section boundary** — and doesn't, only because
  `_render_repo` already quotes excerpts with `> `. That was load-bearing by
  accident before this phase; it is now covered by a test.
- **`extract_one` needed a return type.** Errors and the pruned document have to
  reach two different places (the API response, and `store_profile`'s file
  writes), so it returns an `ExtractionResult` instead of a bare
  `SourceExtraction`. Every caller and test was updated.
- **`MAX_REPOS = 30` still caps the owned tier**, so the 50-repo case that
  triggered this phase actually arrives as 30. The batching is what removes the
  fragility; the cap is a separate, unchanged decision.
- **The success banner was part of the bug.** Listing skipped repos is not
  enough if the headline still reads "Profile ready" — it now reads "ready, with
  N skipped", because the indistinguishability from a clean run is what made
  this expensive to find.

Suite: 230 unit + 43 vitest green. Also verified end-to-end with only the LLM
faked (routes → graph → extraction → splitter → run_store all real): 30 repos in,
1 poisoned, 29 in the profile, `github.json` holding 29 and `github.raw.json`
holding 30, both indexed in `manifest.json`.

### Env vars added in Phase 5

- `GITHUB_REPOS_PER_EXTRACTION` (default `10`) — repos per extraction call for
  a GitHub source; smaller = more calls, less output-truncation risk.
- `GITHUB_TOKEN` is **unchanged but demoted** to a fallback: the per-request
  `github_token` form field wins when supplied.

### Note recorded, not changed (operator config)

The failing run used `deepseek-v4-flash` at `temperature=0.9` and
`max_tokens=16384` for **every** stage. Structured-output stages (extraction,
synthesis, validation) want a low temperature; sampling a tool call at 0.9 makes
"no tool call returned" markedly more likely. Record this as guidance in
`OPERATIONS.md`; **do not** change the user's `.env` and do not add a
per-stage temperature knob in this phase.

### Docs to update (mandatory per CLAUDE.md)

- `TECHNICAL-DESIGN.md` — batched per-repo GitHub extraction, `source_errors`
  propagation, the dual `github.json` / `github.raw.json` archive.
- `API-REFERENCE.md` — `github_token` form field on `POST /ingest`,
  `source_errors` in the response, the `warning` SSE event.
- `OPERATIONS.md` — `GITHUB_REPOS_PER_EXTRACTION`, token now per-request with
  env as fallback, the low-temperature guidance above.
- `PRODUCT-GUIDE.md` — multi-file staging, per-username tokens, visible
  skipped-repo reporting.
- `HISTORY.md` — one entry per change, newest first.

---

## Phase 6 — UI state lifecycle and profile visibility (findings from the Phase 5 UI run) — **planned**

Four defects reported on 2026-07-22 after driving the post-Phase-5 UI. Two are
about **state that never gets cleared**, one is about **a transient failure
destroying loaded data**, and one is about **data the profile holds but the
screen never draws**. None of them are backend bugs — every one was reproduced
or ruled out against a live API before being written down.

### Evidence gathered before planning

- `GET /profile/CVs-Only` → **HTTP 200, 31,829 bytes**;
  `GET /profile/github-only` → **HTTP 200, 34,594 bytes**. The store, the route
  and both stored profiles are healthy.
- Headless-Chrome (CDP) replay of "type an id → click Load" against **both**
  deployment shapes — the built bundle served by the API (`main.py` mounts
  `dist/` at `/`) and the Vite dev server proxying to the API — issues **exactly
  one** `GET /profile/CVs-Only`, gets 200, and the panel is still showing the
  profile at t+0.5s, +1s, +2s and +4s. **The load path itself is not broken**;
  the reported failure is a *second, later* request that fails only in the
  reporter's environment (see 6.c).
- `data/profiles/github-only/v2.json` holds **56 `projects`**, every one with
  `source: "github:thomas-choi"`, alongside 5 experiences and 52 skills. The
  repos are in the profile. `ProfilePanel.tsx` has no `projects` markup at all —
  nor `education`, `certifications` or `contact` (see 6.d).

### 6.a — A Reset button that clears the whole screen

**Problem.** There is no way to start over short of reloading the browser. Every
piece of session state is component-local and unreachable from the parent:
`App.tsx` owns `profileId` / `profileIdInput`; `SourcesPanel` owns the staged CV
and LinkedIn files, `githubUsername`, `githubToken`, `freeText`, `profileId`,
`progress`, `liveWarnings` and its mutation result; `ProfilePanel` owns `draft`
and its save mutation; `TailorPanel` owns `jobPost`, `render`, `coverLetter` and
`result`. Passing a "cleared" prop down would mean threading a reset through
four components and a dozen `useState`s.

1. `frontend/src/App.tsx` — hold a `sessionKey` counter and render every panel
   with `key={sessionKey}`. Bumping it makes React unmount and remount the
   subtree, which discards **all** of the state above in one line and cannot
   drift as panels gain fields. Reset `profileId` and `profileIdInput` in the
   same handler.
2. Clear the query cache too — `useQueryClient().clear()` — otherwise the
   remounted `ProfilePanel` re-renders the previous profile instantly from
   cache; a "Reset" that leaves the old data on screen is worse than none.
3. The button is **destructive** (unsaved profile edits, a staged upload set,
   an entered token), so guard it with a `window.confirm`. Place it in the
   header next to the load form, `type="button"`, labelled "Clear everything".
4. Clearing the GitHub token field is part of the point, and comes free with
   the remount — `SourcesPanel` deliberately keeps it in state only.

**Tests:** `frontend/src/__tests__/App.test.tsx` (new) — with `confirm` stubbed
true, typing into the free-text box, loading a profile, then clicking "Clear
everything" leaves the active-profile line gone and the inputs empty; with
`confirm` stubbed false nothing changes.

### 6.b — Clearing the screen when a new profile is built

**Problem.** "Build profile" starts a new profile but the previous one's output
stays on screen. Three distinct stale-state paths, all confirmed by reading the
components:

1. `ProfilePanel` sets `draft` only *when new data arrives*
   ([ProfilePanel.tsx:25-27](frontend/src/panels/ProfilePanel.tsx#L25-L27)), so
   between the id changing and the fetch landing the panel renders the **old
   profile's** headline, summary and conflicts under the **new** id.
2. `TailorPanel.result` is never reset on a profile change, so the tailored CV,
   the bullet diff, a pending review and the download links from the *previous*
   profile stay visible — and those links carry the old `tailor_id`, so they
   download the old CV.
3. `SourcesPanel` keeps the staged files after a successful run. Clicking
   "Build profile" again silently re-ingests the same CVs into another profile.

**Fix.** Split by what the state is *for*:

- **Downstream panels are keyed by the profile they describe.** Render
  `<ProfilePanel key={...} />` and `<TailorPanel key={...} />` with a key
  combining `sessionKey` and `profileId`. A new profile id remounts both, which
  fixes (1) and (2) together — no `useEffect` reset to keep in sync, and no
  window where old data is drawn under a new id.
- **Inputs clear on success, not on click.** In `SourcesPanel`'s `onSuccess`,
  clear `cvFiles`, `linkedinFiles`, `freeText`, `githubToken` and the "add to
  an existing profile" id, keeping the progress list and the outcome banner
  (including `skipped`) — a *failed* run must keep the user's staged files, or
  they re-pick every file after a 500.

**Tests:** `SourcesPanel.test.tsx` — staged files are gone after a successful
ingest and still staged after a failed one; the skipped/success banner survives
the clear. `App.test.tsx` — a tailor result rendered for profile A is absent
after the active profile becomes B.

### 6.c — "Could not load X: Failed to fetch" wipes a profile that loaded fine

**Problem, precisely.** The reported sequence — profile appears, then ~1s later
the panel is replaced by `Could not load CVs-Only: Failed to fetch` — is two
separate things, and only the second is a bug we can fix blind:

- `Failed to fetch` is a `TypeError` from `fetch` — a **transport** failure
  (connection reset, DNS, proxy hang-up, browser offline). It is never an HTTP
  status: a 404 would render `profile … not found` from FastAPI's `detail`.
  This did not reproduce against either deployment shape locally, so it is
  environment-specific (dev-server proxy to a remote/Docker API, VPN, or a
  wifi/sleep blip are the candidates).
- **Ours regardless:**
  [ProfilePanel.tsx:44-51](frontend/src/panels/ProfilePanel.tsx#L44-L51) checks
  `query.isError` **before** `draft`, so *any* failure — including a background
  refetch that React Query keeps the previous `data` through — erases a profile
  that is already loaded and possibly edited. A one-second network blip costs
  the user their work. The in-file comment justifies the ordering by the
  first-load case only; that case is `isError && !draft`.
- **Why a second request happens at all:** `main.tsx:11` sets only
  `refetchOnWindowFocus: false` and `retry: false`. The default `staleTime: 0`
  marks the response stale the moment it lands, so every new observer of
  `["profile", id]` refetches — and there are two observers, `ProfilePanel` and
  `TailorPanel`. `refetchOnReconnect` is still on by default, so any
  online/offline flicker refires the query, which matches the "a second later"
  timing. With `retry: false` the first blip goes straight to the error state.
  The comment above that config ("nothing is refetched behind their back") does
  not describe what it currently does.
- `getProfile` ignores the `AbortSignal` React Query passes its query function,
  so a *cancelled* request surfaces as a failure rather than being discarded.

1. `frontend/src/panels/ProfilePanel.tsx` — the fatal branch becomes
   `query.isError && !draft`. When a draft exists, render a non-destructive
   `role="alert"` banner above the still-editable profile ("Could not refresh …
   — showing the last loaded copy") with a **Retry** button calling
   `query.refetch()`.
2. `frontend/src/panels/TailorPanel.tsx` — same treatment for its
   `profileQuery`: today a failed profile fetch silently renders an **empty
   diff**, which reads as "nothing changed" rather than "the comparison is
   missing".
3. `frontend/src/main.tsx` — make the defaults match the intent: `staleTime`
   30s (profiles change only when this user changes them, and the save path
   already calls `invalidateQueries`), `refetchOnReconnect: false`, and
   `retry: 1` so a single transport blip retries instead of erroring.
4. `frontend/src/lib/api.ts` — thread React Query's `signal` into `getProfile`
   / `getReview` and into `request`, and wrap a caught `TypeError` as
   `Could not reach the API (<path>) — is the server running?`. The next report
   then distinguishes transport from HTTP without a devtools session.
5. **Still open, needs the reporter:** reproduce with the Network tab recording
   and note the failing request's URL, initiator and status ("(failed)" vs a
   code). Record the answer here — if it is the dev-server proxy dropping the
   connection to a remote API, the fix is in `vite.config.ts` (proxy
   `timeout`/`proxyTimeout` and an `error` handler that returns 502 instead of
   destroying the socket), not in the panels.

**Tests:** `ProfilePanel.test.tsx` — a query that succeeds and then fails on
refetch still renders the profile fields plus the banner, and Retry re-issues
the request; a query that fails on first load still renders the fatal message.
`api.test.ts` — a `fetch` that rejects with `TypeError` produces the
"could not reach the API" message; the abort signal is forwarded.

### 6.d — GitHub projects (and education, certifications, contact) are never drawn — **implemented 2026-07-22**

**Problem.** `github-only/v2.json` holds 56 GitHub repos in `projects` — the
Phase 5.c work put them there correctly — but `ProfilePanel` renders only
`name`, `headline`, `summary_narrative`, `conflicts`, `experiences` and
`skills`. `projects`, `education`, `certifications` and `contact` have **no
markup anywhere in the frontend**. A GitHub-only ingest therefore looks like it
produced nothing, which is exactly the symptom Phase 5.c was supposed to have
made impossible to mistake. The same blindness reaches the tailored CV:
`TailoredCV.selected_projects` is validated (`validation.py:113`), can be
removed at review (`review.py:201`) and **is rendered into the `.docx`**
(`docx_renderer.py:140`) — but `TailorPanel` never displays it, so a person
approves a document containing projects they were never shown.

1. `frontend/src/panels/ProfilePanel.tsx` — add a **Projects** section after
   Experience: count in the heading, one entry per project with the name (an
   `<a href>` when `url` is set, `rel="noopener noreferrer"`), the description,
   the `technologies` as a muted list, and the `source` badge already used by
   experience entries. Muted "No projects" when empty, so an empty list is
   visibly empty rather than absent.
2. Same panel — small **Education**, **Certifications** and **Contact**
   sections. `education` is `list[dict]` in the schema with no fixed shape, so
   render its entries defensively (known keys first, then any remaining
   key/value pairs) rather than assuming a field set the extractor does not
   guarantee.
3. Long lists need to stay reviewable: 56 projects at full height buries the
   Save button. Render the first 10 with a "Show all 56" toggle; keep the
   toggle state local to the section.
4. `frontend/src/panels/TailorPanel.tsx` — render `selected_projects` in the
   result, in the same order as the `.docx`, so review matches the artefact.
5. `frontend/src/lib/diff.ts` — projects are currently outside the diff
   entirely. Extend `diffExperiences`'s sibling coverage with a project-level
   comparison (profile projects vs. selected ones: kept / dropped / not in
   profile), reusing the existing validation-flag lookup so a fabricated
   project is marked in the UI exactly as a fabricated bullet is.

**Tests:** `ProfilePanel.test.tsx` — a profile with GitHub projects lists them
with linked names and technologies, the toggle reveals the rest, and an empty
list renders the muted placeholder. `TailorPanel.test.tsx` — selected projects
appear in the result; `diff.test.ts` — a project present in the tailored CV but
absent from the profile is marked as flagged.

**As built.** 12 new vitest cases (55 green, from 43); the 230 Python unit tests
are untouched and still green. One thing the real data changed: `education`
entries from the CV extractor carry a `location`, so it joined the familiar-key
list rather than being pushed into the leftover line. Verified in headless
Chrome against the stored profiles rather than fixtures only — `github-only`
renders "Projects (56)", ten at a time, "Show all 56" expanding to 56, each
repo linked with `rel="noopener noreferrer"`; `CVs-Only` renders its 6 education
entries, 3 certifications and 4 contact fields. Step 5 (the project diff) landed
as `diffProjects`, keyed on the lower-cased name to match the server.

### Sequencing

6.d first (pure addition, no state semantics touched), then 6.b, then 6.a
(6.a's remount key subsumes 6.b's), then 6.c — its panel changes conflict with
6.b's remount work, so it lands on top rather than under.

### Docs to update (mandatory per CLAUDE.md)

- `PRODUCT-GUIDE.md` — the reset/clear behaviour, what survives a failed run,
  and that projects/education/certifications are now visible before download.
- `TECHNICAL-DESIGN.md` §10 — panel state lifecycle: what is keyed on the
  active profile, what the query cache holds, and the non-destructive refetch
  error rule.
- `API-REFERENCE.md` — no API change (frontend-only phase); record that line.
- `OPERATIONS.md` — only if 6.c step 5 ends in a `vite.config.ts` proxy change.
- `HISTORY.md` — one entry per change, newest first.

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
- **Phase 5:** repeat the run that found the bugs — stage `CV.docx` and `CV.pdf` in **two separate picks** plus a LinkedIn export and a GitHub username with a per-request token — and confirm: both CVs appear in `data/sources/{run_id}/cv/` under distinct names and distinct source ids; the resulting `CareerProfile` contains GitHub projects (`projects` is non-empty when the account has repos); any repo that failed extraction is listed by name in the UI, absent from `github.json`, and still present in `github.raw.json`; and the token appears in no log line and in no file under `data/`.
- **Phase 6:** load `github-only` in the browser → its 56 GitHub projects are
  listed with links and technologies; click "Build profile" with new sources →
  the previous profile's fields, tailored CV and download links are gone before
  the new ones appear; click "Clear everything" → every input, the active
  profile line and the tailor result are empty; kill the API mid-session and
  refetch → the loaded profile stays on screen under a retryable banner instead
  of being replaced by the error.
