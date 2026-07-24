import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

import type { CareerProfile, Project, ReviewRequest, TailorResponse, UserPublic } from "../lib/types";

/** Render inside a fresh QueryClient (no retries — a failure must surface now). */
export function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

export function userFixture(overrides: Partial<UserPublic> = {}): UserPublic {
  return { email: "alice@example.com", first_name: "Alice", last_name: "Smith", ...overrides };
}

/**
 * Stub `global.fetch` for the auth flow. `/auth/me` resolves to a signed-in
 * user by default (so the panel suites that now sit behind the gate keep
 * rendering the app); pass `{ me: 401 }` to render the signed-out screens.
 * Other paths are dispatched through `handlers`, else a 200 empty body.
 */
export function stubAuthFetch(options: {
  me?: UserPublic | 401;
  handlers?: Record<string, () => Response | Promise<Response>>;
} = {}) {
  const me = options.me ?? userFixture();
  const handlers = options.handlers ?? {};
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    if (path === "/auth/me") {
      return me === 401
        ? new Response(JSON.stringify({ detail: "not authenticated" }), { status: 401 })
        : new Response(JSON.stringify(me), { status: 200 });
    }
    for (const [prefix, handler] of Object.entries(handlers)) {
      if (path.startsWith(prefix)) return handler();
    }
    return new Response(JSON.stringify({}), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

export function profileFixture(overrides: Partial<CareerProfile> = {}): CareerProfile {
  return {
    name: "Alice Smith",
    headline: "Senior Engineer",
    contact: { email: "alice@example.com" },
    experiences: [
      {
        company: "Acme Corp",
        title: "Senior Engineer",
        start_date: "2020",
        end_date: "2024",
        location: null,
        bullets: [
          "Built a distributed trading backtester in Python",
          "Led migration of the data pipeline to PostgreSQL",
        ],
        source: "cv_docx:resume.docx",
      },
    ],
    projects: [],
    education: [],
    skills: [{ name: "Python", category: "language", evidence_count: 2 }],
    certifications: [],
    summary_narrative: "Alice is a senior engineer.",
    raw_source_map: {},
    conflicts: [
      {
        field: "experience.start_date",
        description: "CV and LinkedIn disagree on start date",
        values: { "cv_docx:resume.docx": "2020", "linkedin:export.zip": "2019" },
        resolution: null,
      },
    ],
    ...overrides,
  };
}

/** A project as a GitHub ingest produces it (`source: "github:<login>"`). */
export function projectFixture(overrides: Partial<Project> = {}): Project {
  return {
    name: "myFinData",
    description: "A financial data pipeline for daily stock prices",
    technologies: ["Python", "C++"],
    role: null,
    url: "https://github.com/alice/myFinData",
    source: "github:alice",
    ...overrides,
  };
}

export function reviewFixture(): ReviewRequest {
  return {
    tailor_id: "t-1",
    brief: "One claim could not be traced.",
    items: [
      {
        id: "flag-0",
        item: "Ran a team of 40 engineers",
        kind: "bullet",
        reason: "No profile bullet mentions managing a team",
        similarity: 0.21,
        closest_profile_text: "Led migration of the data pipeline to PostgreSQL",
        source: "cv_docx:resume.docx",
      },
      {
        id: "flag-1",
        item: "Kubernetes",
        kind: "skill",
        reason: "Skill not present in the career profile",
        similarity: null,
        closest_profile_text: null,
        source: null,
      },
    ],
  };
}

export function tailorFixture(overrides: Partial<TailorResponse> = {}): TailorResponse {
  return {
    profile_id: "alice",
    tailor_id: "t-1",
    job_requirements: {
      title: "Backend Engineer",
      company: null,
      required_skills: ["Python"],
      preferred_skills: [],
      responsibilities: [],
      seniority: null,
      keywords_for_ats: [],
    },
    tailored_cv: {
      headline: "Senior Backend Engineer",
      summary: "A concise pitch.",
      selected_experiences: [
        {
          company: "Acme Corp",
          title: "Senior Engineer",
          start_date: "2020",
          end_date: "2024",
          location: null,
          bullets: [
            "Built a distributed trading backtester in Python",
            "Ran a team of 40 engineers",
          ],
          source: "cv_docx:resume.docx",
        },
      ],
      selected_projects: [],
      highlighted_skills: ["Python", "Kubernetes"],
      relevance_notes: {},
    },
    validation: {
      passed: false,
      needs_review: true,
      flags: [
        {
          item: "Ran a team of 40 engineers",
          kind: "bullet",
          reason: "No profile bullet mentions managing a team",
          similarity: 0.21,
        },
      ],
    },
    cover_letter: null,
    documents: [],
    render_skipped: null,
    review_required: true,
    review: reviewFixture(),
    review_url: "/tailor/t-1/review",
    ...overrides,
  };
}
