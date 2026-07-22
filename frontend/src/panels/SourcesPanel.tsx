/** Panel 1 — upload sources and watch the ingestion graph run (SSE). */

import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { ingest, subscribeToIngest } from "../lib/api";
import type { IngestResponse } from "../lib/types";

const NODE_LABELS: Record<string, string> = {
  ingest_sources: "Reading sources",
  extract_source: "Extracting facts per source",
  synthesize_profile: "Synthesizing the profile",
  store_profile: "Storing the profile",
};

function newJobId(): string {
  return `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Identity of a staged file — two picks of the same file must not stack up. */
function fileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

/**
 * Add a pick to the already-staged files.
 *
 * A file input reports only the files chosen in *that* dialog, so replacing the
 * list on every `change` silently drops everything picked before — the defect
 * that lost a second CV in the first end-to-end run.
 */
function addFiles(staged: File[], picked: File[]): File[] {
  const seen = new Set(staged.map(fileKey));
  return [...staged, ...picked.filter((file) => !seen.has(fileKey(file)))];
}

/** The files staged for one input, each removable before the run starts. */
function StagedFiles({
  label,
  files,
  onRemove,
}: {
  label: string;
  files: File[];
  onRemove: (key: string) => void;
}) {
  if (files.length === 0) return null;
  return (
    <ul className="entries" aria-label={label}>
      {files.map((file) => (
        <li key={fileKey(file)}>
          {file.name}{" "}
          <button type="button" onClick={() => onRemove(fileKey(file))}>
            Remove {file.name}
          </button>
        </li>
      ))}
    </ul>
  );
}

interface Props {
  onIngested: (profileId: string) => void;
}

export function SourcesPanel({ onIngested }: Props) {
  const [cvFiles, setCvFiles] = useState<File[]>([]);
  const [linkedinFiles, setLinkedinFiles] = useState<File[]>([]);
  const [githubUsername, setGithubUsername] = useState("");
  // Held in component state only — never localStorage, never a form default.
  const [githubToken, setGithubToken] = useState("");
  const [freeText, setFreeText] = useState("");
  const [profileId, setProfileId] = useState("");
  const [progress, setProgress] = useState<string[]>([]);
  const [liveWarnings, setLiveWarnings] = useState<string[]>([]);
  const unsubscribe = useRef<(() => void) | null>(null);

  const mutation = useMutation<IngestResponse, Error>({
    mutationFn: () => {
      const jobId = newJobId();
      setProgress([]);
      setLiveWarnings([]);
      // Subscribe before POSTing so the first node event is not missed.
      unsubscribe.current = subscribeToIngest(
        jobId,
        (node) => setProgress((seen) => [...seen, node]),
        () => {
          unsubscribe.current = null;
        },
        (message) => setLiveWarnings((seen) => [...seen, message]),
      );
      return ingest({
        files: cvFiles,
        linkedinExports: linkedinFiles,
        githubUsername,
        githubToken,
        freeText,
        profileId,
        jobId,
      });
    },
    onSettled: () => {
      unsubscribe.current?.();
      unsubscribe.current = null;
    },
    onSuccess: (data) => onIngested(data.profile_id),
  });

  // The response is authoritative once it lands; the SSE warnings only fill the
  // gap while the run is still going.
  const skipped = mutation.data?.source_errors ?? [];

  const nothingToSend =
    cvFiles.length === 0 &&
    linkedinFiles.length === 0 &&
    !githubUsername.trim() &&
    !freeText.trim();

  return (
    <section className="panel" aria-labelledby="sources-heading">
      <h2 id="sources-heading">1 · Sources</h2>

      <label htmlFor="cv-files">CV files (.docx, .pdf)</label>
      <input
        id="cv-files"
        type="file"
        multiple
        accept=".docx,.pdf"
        onChange={(event) => {
          // Read before clearing: the state updater runs later, by which time
          // `event.target.files` is already empty.
          const picked = Array.from(event.target.files ?? []);
          // Clear the input so re-picking a file just removed fires `change` again.
          event.target.value = "";
          setCvFiles((staged) => addFiles(staged, picked));
        }}
      />
      <StagedFiles
        label="Staged CV files"
        files={cvFiles}
        onRemove={(key) =>
          setCvFiles((staged) => staged.filter((file) => fileKey(file) !== key))
        }
      />

      <label htmlFor="linkedin-files">LinkedIn data export (.zip or .csv)</label>
      <input
        id="linkedin-files"
        type="file"
        multiple
        accept=".zip,.csv"
        onChange={(event) => {
          const picked = Array.from(event.target.files ?? []);
          event.target.value = "";
          setLinkedinFiles((staged) => addFiles(staged, picked));
        }}
      />
      <StagedFiles
        label="Staged LinkedIn exports"
        files={linkedinFiles}
        onRemove={(key) =>
          setLinkedinFiles((staged) => staged.filter((file) => fileKey(file) !== key))
        }
      />

      <label htmlFor="github-username">GitHub username</label>
      <input
        id="github-username"
        value={githubUsername}
        onChange={(event) => setGithubUsername(event.target.value)}
        placeholder="octocat"
      />

      <label htmlFor="github-token">GitHub token (optional)</label>
      <input
        id="github-token"
        type="password"
        autoComplete="off"
        value={githubToken}
        onChange={(event) => setGithubToken(event.target.value)}
      />
      <p className="muted">
        A token for the username above also unlocks their private repositories and
        organization memberships. A token for anyone else only raises rate limits. It is
        used for this request and never stored.
      </p>

      <label htmlFor="free-text">Anything else (pasted notes, a summary)</label>
      <textarea
        id="free-text"
        rows={4}
        value={freeText}
        onChange={(event) => setFreeText(event.target.value)}
      />

      <label htmlFor="profile-id">
        Add to an existing profile (optional — blank creates a new one)
      </label>
      <input
        id="profile-id"
        value={profileId}
        onChange={(event) => setProfileId(event.target.value)}
        placeholder="alice-main"
      />

      <button
        type="button"
        disabled={nothingToSend || mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        {mutation.isPending ? "Ingesting…" : "Build profile"}
      </button>

      {progress.length > 0 && (
        <ol className="progress" aria-label="Ingestion progress">
          {progress.map((node, index) => (
            <li key={`${node}-${index}`}>{NODE_LABELS[node] ?? node}</li>
          ))}
        </ol>
      )}

      {skipped.length === 0 && liveWarnings.length > 0 && (
        <ul className="entries warn" aria-label="Skipped so far">
          {liveWarnings.map((message, index) => (
            <li key={`${message}-${index}`}>{message}</li>
          ))}
        </ul>
      )}

      {skipped.length > 0 && (
        <div className="warn">
          <p>
            <strong>Skipped {skipped.length}</strong> — everything else was read
            normally, but these did not make it into the profile:
          </p>
          <ul className="entries" aria-label="Skipped items">
            {skipped.map((error, index) => (
              <li key={`${error.repo ?? error.source}-${index}`}>
                <strong>{error.repo ?? error.source}</strong> — {error.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {mutation.isError && <p role="alert">Ingestion failed: {mutation.error.message}</p>}
      {mutation.isSuccess && (
        <p className="ok">
          Profile <strong>{mutation.data.profile_id}</strong> v{mutation.data.version} ready
          {skipped.length > 0 ? `, with ${skipped.length} skipped.` : "."}
        </p>
      )}
    </section>
  );
}
