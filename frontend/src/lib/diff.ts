/**
 * Pairing a tailored CV against the profile it came from, for the diff view.
 *
 * The similarity here is for *display* only — it decides how a bullet is
 * labelled on screen. The authoritative judgement is the server's validation
 * gate (difflib + an LLM cross-check); a bullet it flagged is shown as flagged
 * whatever this scores.
 */

import type {
  CareerProfile,
  Experience,
  Project,
  TailoredCV,
  ValidationResult,
} from "./types";

/** Dice coefficient over character bigrams — cheap, order-insensitive, 0..1. */
export function similarity(a: string, b: string): number {
  const left = a.trim().toLowerCase();
  const right = b.trim().toLowerCase();
  if (!left || !right) return 0;
  if (left === right) return 1;
  const bigrams = (s: string) => {
    const out = new Map<string, number>();
    for (let i = 0; i < s.length - 1; i += 1) {
      const pair = s.slice(i, i + 2);
      out.set(pair, (out.get(pair) ?? 0) + 1);
    }
    return out;
  };
  const first = bigrams(left);
  const second = bigrams(right);
  let shared = 0;
  first.forEach((count, pair) => {
    shared += Math.min(count, second.get(pair) ?? 0);
  });
  const total = left.length - 1 + (right.length - 1);
  return total > 0 ? (2 * shared) / total : 0;
}

export type BulletStatus = "unchanged" | "reworded" | "flagged" | "new";

export interface BulletDiff {
  tailored: string;
  original: string | null;
  status: BulletStatus;
  similarity: number;
}

export interface ExperienceDiff {
  key: string;
  company: string;
  title: string;
  bullets: BulletDiff[];
  /** Bullets in the profile that the tailored CV left out. */
  dropped: string[];
}

const REWORD_THRESHOLD = 0.55; // mirrors VALIDATION_SIMILARITY_THRESHOLD

function closest(bullet: string, candidates: string[]): { text: string | null; score: number } {
  let best: string | null = null;
  let score = 0;
  candidates.forEach((candidate) => {
    const value = similarity(bullet, candidate);
    if (value > score) {
      best = candidate;
      score = value;
    }
  });
  return { text: best, score };
}

function matchExperience(
  experience: Experience,
  profile: CareerProfile,
): Experience | undefined {
  return profile.experiences.find(
    (candidate) =>
      candidate.company.toLowerCase() === experience.company.toLowerCase() &&
      candidate.title.toLowerCase() === experience.title.toLowerCase(),
  );
}

/**
 * Line up each tailored experience with its profile original.
 *
 * A bullet is `unchanged` when it appears verbatim in the profile, `reworded`
 * when it is close to one, `flagged` when the validation gate flagged it, and
 * `new` when neither — i.e. it has no visible origin, which is exactly what a
 * reviewer needs to see.
 */
export function diffExperiences(
  profile: CareerProfile,
  cv: TailoredCV,
  validation: ValidationResult,
): ExperienceDiff[] {
  const flagged = new Set(
    validation.flags.filter((flag) => flag.kind === "bullet").map((flag) => flag.item),
  );
  return cv.selected_experiences.map((experience) => {
    const original = matchExperience(experience, profile);
    const originalBullets = original?.bullets ?? [];
    const used = new Set<string>();
    const bullets = experience.bullets.map((bullet) => {
      const { text, score } = closest(bullet, originalBullets);
      if (text) used.add(text);
      let status: BulletStatus;
      if (flagged.has(bullet)) status = "flagged";
      else if (score === 1) status = "unchanged";
      else if (score >= REWORD_THRESHOLD) status = "reworded";
      else status = "new";
      return { tailored: bullet, original: text, status, similarity: score };
    });
    return {
      key: `${experience.company}::${experience.title}`,
      company: experience.company,
      title: experience.title,
      bullets,
      dropped: originalBullets.filter((bullet) => !used.has(bullet)),
    };
  });
}

export type ProjectStatus = "kept" | "flagged";

export interface ProjectDiff {
  project: Project;
  status: ProjectStatus;
}

export interface ProjectDiffResult {
  selected: ProjectDiff[];
  /** Profile projects the tailored CV left out, by name. */
  dropped: string[];
}

/**
 * Line up the tailored CV's projects with the profile's.
 *
 * Matching is by name, case-insensitively — the same key the server's
 * validation gate and the review node use, so a project this marks `flagged` is
 * the one the gate flagged, never a near-miss of our own invention.
 */
export function diffProjects(
  profile: CareerProfile,
  cv: TailoredCV,
  validation: ValidationResult,
): ProjectDiffResult {
  const flagged = new Set(
    validation.flags
      .filter((flag) => flag.kind === "project")
      .map((flag) => flag.item.toLowerCase()),
  );
  const selectedNames = new Set(
    cv.selected_projects.map((project) => project.name.toLowerCase()),
  );
  return {
    selected: cv.selected_projects.map((project) => ({
      project,
      status: flagged.has(project.name.toLowerCase()) ? "flagged" : "kept",
    })),
    dropped: profile.projects
      .filter((project) => !selectedNames.has(project.name.toLowerCase()))
      .map((project) => project.name),
  };
}
