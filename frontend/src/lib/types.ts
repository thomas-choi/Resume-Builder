/** Mirrors src/models/schemas.py — keep the two in step. */

export interface Experience {
  company: string;
  title: string;
  start_date: string | null;
  end_date: string | null;
  location: string | null;
  bullets: string[];
  source: string;
}

export interface Project {
  name: string;
  description: string;
  technologies: string[];
  role: string | null;
  url: string | null;
  source: string;
}

export interface Skill {
  name: string;
  category: string;
  evidence_count: number;
}

/** A cross-source disagreement. Surfaced for a person to resolve, never guessed. */
export interface Conflict {
  field: string;
  description: string;
  values: Record<string, string>;
  /** The value the person picked in the review UI; null while unresolved. */
  resolution: string | null;
}

export interface CareerProfile {
  name: string;
  headline: string | null;
  contact: Record<string, string>;
  experiences: Experience[];
  projects: Project[];
  education: Record<string, unknown>[];
  skills: Skill[];
  certifications: string[];
  summary_narrative: string;
  raw_source_map: Record<string, string>;
  conflicts: Conflict[];
}

export interface JobRequirements {
  title: string;
  company: string | null;
  required_skills: string[];
  preferred_skills: string[];
  responsibilities: string[];
  seniority: string | null;
  keywords_for_ats: string[];
}

export interface TailoredCV {
  headline: string;
  summary: string;
  selected_experiences: Experience[];
  selected_projects: Project[];
  highlighted_skills: string[];
  relevance_notes: Record<string, string>;
}

export interface ValidationFlag {
  item: string;
  kind: string;
  reason: string;
  similarity: number | null;
}

export interface ValidationResult {
  passed: boolean;
  flags: ValidationFlag[];
  needs_review: boolean;
}

export interface ReviewItem {
  id: string;
  item: string;
  kind: string;
  reason: string;
  similarity: number | null;
  closest_profile_text: string | null;
  source: string | null;
}

export interface ReviewRequest {
  tailor_id: string;
  items: ReviewItem[];
  brief: string;
  pending?: boolean;
}

export interface ReviewDecision {
  approvals: Record<string, boolean>;
  approve_all: boolean;
  notes: string;
}

export interface RenderedDocument {
  kind: string;
  format: string;
  filename: string;
  size_bytes: number;
  url: string;
}

/** One item the extractor could not read — a repo, or a whole source. */
export interface SourceError {
  source: string;
  repo: string | null;
  reason: string;
}

export interface IngestResponse {
  job_id: string;
  run_id: string;
  profile_id: string;
  version: number;
  source_errors: SourceError[];
  profile: CareerProfile;
}

export interface ProfileResponse {
  profile_id: string;
  version: number;
  versions: number[];
  profile: CareerProfile;
}

export interface TailorResponse {
  profile_id: string;
  tailor_id: string;
  job_requirements: JobRequirements;
  tailored_cv: TailoredCV;
  validation: ValidationResult;
  cover_letter: { greeting: string; body_paragraphs: string[]; closing: string } | null;
  documents: RenderedDocument[];
  render_skipped: string | null;
  review_required: boolean;
  review: ReviewRequest | null;
  review_url: string | null;
}
