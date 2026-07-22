/** Panel 2 — review and edit the synthesized profile, resolve its conflicts. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { getProfile, putProfile } from "../lib/api";
import type { CareerProfile, ProfileResponse } from "../lib/types";

interface Props {
  profileId: string | null;
}

export function ProfilePanel({ profileId }: Props) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<CareerProfile | null>(null);

  const query = useQuery<ProfileResponse>({
    queryKey: ["profile", profileId],
    queryFn: () => getProfile(profileId as string),
    enabled: Boolean(profileId),
  });

  // The panel edits a local draft so a slow save never fights the user's typing;
  // a freshly loaded (or newly saved) version replaces it.
  useEffect(() => {
    if (query.data) setDraft(query.data.profile);
  }, [query.data]);

  const save = useMutation({
    mutationFn: () => putProfile(profileId as string, draft as CareerProfile),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profile", profileId] }),
  });

  if (!profileId) {
    return (
      <section className="panel" aria-labelledby="profile-heading">
        <h2 id="profile-heading">2 · Profile</h2>
        <p className="muted">Ingest some sources, or load a profile by id, to start.</p>
      </section>
    );
  }
  // Errors are checked first: a failed load leaves `draft` null forever, so
  // testing for the draft first would render a spinner that never resolves.
  if (query.isError) {
    return (
      <section className="panel" aria-labelledby="profile-heading">
        <h2 id="profile-heading">2 · Profile</h2>
        <p role="alert">Could not load {profileId}: {(query.error as Error).message}</p>
      </section>
    );
  }
  if (!draft) {
    return (
      <section className="panel" aria-labelledby="profile-heading">
        <h2 id="profile-heading">2 · Profile</h2>
        <p>Loading profile…</p>
      </section>
    );
  }

  const unresolved = draft.conflicts.filter((conflict) => conflict.resolution === null);

  const resolveConflict = (index: number, value: string | null) =>
    setDraft({
      ...draft,
      conflicts: draft.conflicts.map((conflict, position) =>
        position === index ? { ...conflict, resolution: value } : conflict,
      ),
    });

  return (
    <section className="panel" aria-labelledby="profile-heading">
      <h2 id="profile-heading">2 · Profile</h2>
      <p className="muted">
        {draft.name} · version {query.data?.version} of {query.data?.versions.length}
      </p>

      <label htmlFor="profile-headline">Headline</label>
      <input
        id="profile-headline"
        value={draft.headline ?? ""}
        onChange={(event) => setDraft({ ...draft, headline: event.target.value })}
      />

      <label htmlFor="profile-summary">Summary</label>
      <textarea
        id="profile-summary"
        rows={4}
        value={draft.summary_narrative}
        onChange={(event) => setDraft({ ...draft, summary_narrative: event.target.value })}
      />

      <h3>
        Conflicts{" "}
        {unresolved.length > 0 && (
          <span className="badge" aria-label={`${unresolved.length} unresolved`}>
            {unresolved.length}
          </span>
        )}
      </h3>
      {draft.conflicts.length === 0 && <p className="muted">No source disagreed.</p>}
      <ul className="conflicts">
        {draft.conflicts.map((conflict, index) => (
          <li key={`${conflict.field}-${index}`} className={conflict.resolution ? "resolved" : ""}>
            <p>
              <strong>{conflict.field}</strong> — {conflict.description}
            </p>
            <div role="group" aria-label={`Resolve ${conflict.field}`}>
              {Object.entries(conflict.values).map(([source, value]) => (
                <button
                  key={source}
                  type="button"
                  aria-pressed={conflict.resolution === value}
                  onClick={() => resolveConflict(index, value)}
                >
                  {value} <span className="muted">({source})</span>
                </button>
              ))}
              {conflict.resolution !== null && (
                <button type="button" onClick={() => resolveConflict(index, null)}>
                  Undo
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>

      <h3>Experience</h3>
      <ul className="entries">
        {draft.experiences.map((experience, index) => (
          <li key={`${experience.company}-${experience.title}-${index}`}>
            <p>
              <strong>{experience.title}</strong> · {experience.company}{" "}
              <span className="muted">
                {experience.start_date} – {experience.end_date ?? "present"} · {experience.source}
              </span>
            </p>
            <ul>
              {experience.bullets.map((bullet, bulletIndex) => (
                <li key={bulletIndex}>{bullet}</li>
              ))}
            </ul>
          </li>
        ))}
      </ul>

      <h3>Skills</h3>
      <p>{draft.skills.map((skill) => skill.name).join(", ") || "—"}</p>

      <button type="button" disabled={save.isPending} onClick={() => save.mutate()}>
        {save.isPending ? "Saving…" : "Save as new version"}
      </button>
      {save.isError && <p role="alert">Save failed: {(save.error as Error).message}</p>}
      {save.isSuccess && <p className="ok">Saved as v{save.data.version}.</p>}
    </section>
  );
}
