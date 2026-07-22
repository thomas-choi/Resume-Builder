import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import type { CareerProfile, ReviewRequest, TailorResponse } from "../lib/types";

/** Render inside a fresh QueryClient (no retries — a failure must surface now). */
export function renderWithClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
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
