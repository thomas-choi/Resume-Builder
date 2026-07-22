/** Thin fetch wrappers over the FastAPI endpoints (same origin — see main.py). */

import type {
  CareerProfile,
  IngestResponse,
  ProfileResponse,
  ReviewDecision,
  ReviewRequest,
  TailorResponse,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    // `fetch` rejects only on a *transport* failure — connection reset, DNS,
    // proxy hang-up, browser offline — and does so with a bare
    // `TypeError: Failed to fetch`, which reads as a bug in this code. An HTTP
    // error is a resolved response handled below, so naming the difference here
    // is what lets the next report say which one it was without devtools.
    if (error instanceof TypeError) {
      throw new Error(`Could not reach the API (${path}) — is the server running?`);
    }
    // An abort is React Query discarding a superseded request, not a failure.
    throw error;
  }
  if (!response.ok) {
    // FastAPI puts the reason in `detail`; surface it rather than a bare status.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* non-JSON error body — keep the status line */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export interface IngestInput {
  files: File[];
  linkedinExports: File[];
  githubUsername: string;
  /** Sent only when non-empty — a secret in transit, never persisted client-side. */
  githubToken: string;
  freeText: string;
  profileId: string;
  jobId: string;
}

export function ingest(input: IngestInput): Promise<IngestResponse> {
  const form = new FormData();
  input.files.forEach((file) => form.append("cv", file));
  input.linkedinExports.forEach((file) => form.append("linkedin_export", file));
  if (input.githubUsername) form.append("github_username", input.githubUsername);
  if (input.githubToken.trim()) form.append("github_token", input.githubToken.trim());
  if (input.freeText.trim()) form.append("free_text", input.freeText);
  if (input.profileId) form.append("profile_id", input.profileId);
  form.append("job_id", input.jobId);
  return request<IngestResponse>("/ingest", { method: "POST", body: form });
}

/**
 * Fetch a stored profile.
 *
 * `signal` is the one React Query hands its query function: forwarding it means
 * a superseded or unmounted request is *cancelled* rather than left to land and
 * be reported as a failure.
 */
export function getProfile(
  profileId: string,
  signal?: AbortSignal,
): Promise<ProfileResponse> {
  return request<ProfileResponse>(`/profile/${encodeURIComponent(profileId)}`, { signal });
}

/** Save an edited profile as a new version (conflict resolution included). */
export function putProfile(
  profileId: string,
  profile: CareerProfile,
): Promise<{ profile_id: string; version: number }> {
  return request(`/profile/${encodeURIComponent(profileId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
}

export interface TailorInput {
  profileId: string;
  jobPost: string;
  render: boolean;
  coverLetter: boolean;
}

export function tailor(input: TailorInput): Promise<TailorResponse> {
  return request<TailorResponse>("/tailor", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      profile_id: input.profileId,
      job_post: input.jobPost,
      render: input.render,
      cover_letter: input.coverLetter,
    }),
  });
}

export function getReview(
  tailorId: string,
  signal?: AbortSignal,
): Promise<ReviewRequest> {
  return request<ReviewRequest>(`/tailor/${encodeURIComponent(tailorId)}/review`, {
    signal,
  });
}

/** Resume the paused run: unapproved items are dropped from the CV server-side. */
export function resumeTailor(
  tailorId: string,
  decision: ReviewDecision,
): Promise<TailorResponse> {
  return request<TailorResponse>(`/tailor/${encodeURIComponent(tailorId)}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
  });
}

/**
 * Subscribe to an ingest run's per-node progress.
 *
 * Returns an unsubscribe function. The stream is opened *before* POST /ingest
 * so no node event is missed (the server creates the queue on either call).
 * `onWarning` receives one line per item the extractor had to skip; the same
 * items come back on the response, so a missed event costs nothing.
 */
export function subscribeToIngest(
  jobId: string,
  onNode: (node: string) => void,
  onDone: () => void,
  onWarning?: (message: string) => void,
): () => void {
  const source = new EventSource(`/ingest/${encodeURIComponent(jobId)}/events`);
  source.addEventListener("node", (event) => onNode((event as MessageEvent).data));
  source.addEventListener("warning", (event) =>
    onWarning?.((event as MessageEvent).data),
  );
  source.addEventListener("done", () => {
    source.close();
    onDone();
  });
  source.onerror = () => {
    source.close();
    onDone();
  };
  return () => source.close();
}
