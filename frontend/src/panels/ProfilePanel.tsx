/** Panel 2 — review and edit the synthesized profile, resolve its conflicts. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { getProfile, putProfile } from "../lib/api";
import type { CareerProfile, ProfileResponse } from "../lib/types";

/** Entries of a long list shown before the reader asks for the rest. */
const PREVIEW_LIMIT = 10;

/**
 * A list that shows its first {@link PREVIEW_LIMIT} entries until asked for all
 * of them.
 *
 * One GitHub source brings back dozens of projects; drawn in full they push the
 * conflicts and the Save button off the screen, so the panel opens with a
 * readable sample and says how many more there are.
 */
function ExpandableList<T>({
  label,
  items,
  renderItem,
}: {
  label: string;
  items: T[];
  renderItem: (item: T, index: number) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? items : items.slice(0, PREVIEW_LIMIT);
  return (
    <>
      <ul className="entries" aria-label={label}>
        {shown.map(renderItem)}
      </ul>
      {items.length > PREVIEW_LIMIT && (
        <button type="button" onClick={() => setExpanded(!expanded)}>
          {expanded ? "Show fewer" : `Show all ${items.length}`}
        </button>
      )}
    </>
  );
}

/** A value from a free-shaped dict, as display text ("" when it has none). */
function asText(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

/** Keys an education entry is *likely* to have, in the order they read best. */
const EDUCATION_KEYS = [
  "degree",
  "field",
  "field_of_study",
  "school",
  "institution",
  "location",
  "start_date",
  "end_date",
  "year",
];

/**
 * One education entry, defensively.
 *
 * `education` is `list[dict]` in the schema with no fixed shape — the extractor
 * guarantees no field set — so the familiar keys are drawn first and whatever
 * else the entry happens to carry is listed as-is rather than silently dropped.
 */
function EducationEntry({ entry }: { entry: Record<string, unknown> }) {
  const headline = EDUCATION_KEYS.map((key) => asText(entry[key]))
    .filter(Boolean)
    .join(" · ");
  const rest = Object.entries(entry).filter(
    ([key, value]) => !EDUCATION_KEYS.includes(key) && asText(value),
  );
  return (
    <li>
      <p>{headline || "(no details)"}</p>
      {rest.length > 0 && (
        <p className="muted">
          {rest.map(([key, value]) => `${key}: ${asText(value)}`).join(" · ")}
        </p>
      )}
    </li>
  );
}

interface Props {
  profileId: string | null;
}

export function ProfilePanel({ profileId }: Props) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<CareerProfile | null>(null);

  const query = useQuery<ProfileResponse>({
    queryKey: ["profile", profileId],
    queryFn: ({ signal }) => getProfile(profileId as string, signal),
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
  // Only a failure with *nothing loaded* is fatal. A first-load failure leaves
  // `draft` null forever, so it must be caught before the loading branch or the
  // panel spins for ever — but once a profile is on screen, a failed background
  // refetch must not erase it (and with it the user's unsaved edits): that
  // renders as the retryable banner below instead.
  if (query.isError && !draft) {
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
      {query.isError && (
        <p role="alert" className="warn">
          Could not refresh {profileId}: {(query.error as Error).message} — showing the
          last loaded copy.{" "}
          <button type="button" onClick={() => query.refetch()}>
            Retry
          </button>
        </p>
      )}
      <p className="muted">
        {draft.name} · version {query.data?.version} of {query.data?.versions.length}
      </p>
      {Object.keys(draft.contact).length > 0 && (
        <p className="muted" aria-label="Contact">
          {Object.entries(draft.contact)
            .map(([field, value]) => `${field}: ${value}`)
            .join(" · ")}
        </p>
      )}

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

      <h3>Projects {draft.projects.length > 0 && <span className="muted">({draft.projects.length})</span>}</h3>
      {draft.projects.length === 0 ? (
        <p className="muted">No projects — a GitHub source contributes one per repository.</p>
      ) : (
        <ExpandableList
          label="Projects"
          items={draft.projects}
          renderItem={(project, index) => (
            <li key={`${project.name}-${index}`} className="project">
              <p>
                <strong>
                  {project.url ? (
                    // Repo links open away from the review screen, which must
                    // keep its unsaved draft.
                    <a href={project.url} target="_blank" rel="noopener noreferrer">
                      {project.name}
                    </a>
                  ) : (
                    project.name
                  )}
                </strong>{" "}
                <span className="muted">{project.source}</span>
              </p>
              {project.description && <p>{project.description}</p>}
              {project.technologies.length > 0 && (
                <p className="muted">{project.technologies.join(", ")}</p>
              )}
            </li>
          )}
        />
      )}

      <h3>Education</h3>
      {draft.education.length === 0 ? (
        <p className="muted">No education entries.</p>
      ) : (
        <ExpandableList
          label="Education"
          items={draft.education}
          renderItem={(entry, index) => <EducationEntry key={index} entry={entry} />}
        />
      )}

      <h3>Skills</h3>
      <p>{draft.skills.map((skill) => skill.name).join(", ") || "—"}</p>

      <h3>Certifications</h3>
      {draft.certifications.length === 0 ? (
        <p className="muted">No certifications.</p>
      ) : (
        <ExpandableList
          label="Certifications"
          items={draft.certifications}
          renderItem={(certification, index) => (
            <li key={`${certification}-${index}`}>{certification}</li>
          )}
        />
      )}

      <button type="button" disabled={save.isPending} onClick={() => save.mutate()}>
        {save.isPending ? "Saving…" : "Save as new version"}
      </button>
      {save.isError && <p role="alert">Save failed: {(save.error as Error).message}</p>}
      {save.isSuccess && <p className="ok">Saved as v{save.data.version}.</p>}
    </section>
  );
}
