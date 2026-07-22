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

interface Props {
  onIngested: (profileId: string) => void;
}

export function SourcesPanel({ onIngested }: Props) {
  const [cvFiles, setCvFiles] = useState<File[]>([]);
  const [linkedinFiles, setLinkedinFiles] = useState<File[]>([]);
  const [githubUsername, setGithubUsername] = useState("");
  const [freeText, setFreeText] = useState("");
  const [profileId, setProfileId] = useState("");
  const [progress, setProgress] = useState<string[]>([]);
  const unsubscribe = useRef<(() => void) | null>(null);

  const mutation = useMutation<IngestResponse, Error>({
    mutationFn: () => {
      const jobId = newJobId();
      setProgress([]);
      // Subscribe before POSTing so the first node event is not missed.
      unsubscribe.current = subscribeToIngest(
        jobId,
        (node) => setProgress((seen) => [...seen, node]),
        () => {
          unsubscribe.current = null;
        },
      );
      return ingest({
        files: cvFiles,
        linkedinExports: linkedinFiles,
        githubUsername,
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
        onChange={(event) => setCvFiles(Array.from(event.target.files ?? []))}
      />

      <label htmlFor="linkedin-files">LinkedIn data export (.zip or .csv)</label>
      <input
        id="linkedin-files"
        type="file"
        multiple
        accept=".zip,.csv"
        onChange={(event) => setLinkedinFiles(Array.from(event.target.files ?? []))}
      />

      <label htmlFor="github-username">GitHub username</label>
      <input
        id="github-username"
        value={githubUsername}
        onChange={(event) => setGithubUsername(event.target.value)}
        placeholder="octocat"
      />

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

      {mutation.isError && <p role="alert">Ingestion failed: {mutation.error.message}</p>}
      {mutation.isSuccess && (
        <p className="ok">
          Profile <strong>{mutation.data.profile_id}</strong> v{mutation.data.version} ready.
        </p>
      )}
    </section>
  );
}
