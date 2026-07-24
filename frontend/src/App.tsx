/**
 * Three-panel review UI (design doc §10): sources → profile → tailor.
 *
 * The panels share one piece of state, the active `profile_id`: ingestion
 * produces it, the profile panel edits that profile, and the tailor panel
 * tailors from it.
 *
 * Everything else each panel holds is component-local — staged files, a token,
 * an edited draft, a tailored result — so the two ways of starting over are
 * expressed as remounts rather than as a reset threaded through a dozen
 * `useState`s that would drift as the panels gain fields:
 *
 * - `sessionKey` bumps on "Clear everything" and remounts the whole screen.
 * - The downstream panels are additionally keyed on the active profile, so a
 *   new profile can never be drawn wearing the previous one's headline,
 *   diff or download links.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { listProfiles } from "./lib/api";
import { ProfilePanel } from "./panels/ProfilePanel";
import { SourcesPanel } from "./panels/SourcesPanel";
import { TailorPanel } from "./panels/TailorPanel";
import { TutorialPage } from "./panels/TutorialPage";

type View = "builder" | "tutorial";

export function App() {
  const queryClient = useQueryClient();
  const [profileId, setProfileId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [sessionKey, setSessionKey] = useState(0);
  const [view, setView] = useState<View>("builder");

  // The picker lists the signed-in user's profiles. It is refreshed after an
  // ingest (a newly built profile must appear) via query invalidation.
  const profilesQuery = useQuery({
    queryKey: ["profiles"],
    queryFn: ({ signal }) => listProfiles(signal),
  });
  const profiles = profilesQuery.data?.profiles ?? [];

  // Set the active profile from an ingest, and pull the new id into the picker.
  function activateProfile(id: string) {
    setProfileId(id);
    setSelectedId(id);
    queryClient.invalidateQueries({ queryKey: ["profiles"] });
  }

  function clearEverything() {
    // Destructive: unsaved profile edits, staged uploads and a typed token all
    // go with it.
    const confirmed = window.confirm(
      "Clear everything? Staged files, unsaved profile edits and the tailored CV will be discarded.",
    );
    if (!confirmed) return;
    // Without this the remounted profile panel re-renders the old profile
    // instantly from cache — a "Clear" that leaves the data on screen is worse
    // than none.
    queryClient.clear();
    setProfileId(null);
    setSelectedId("");
    setSessionKey((key) => key + 1);
  }

  const downstreamKey = `${sessionKey}:${profileId ?? ""}`;

  return (
    <div className="app">
      <header>
        <h1>Resume Builder</h1>
        <nav className="tabs">
          <button
            type="button"
            className={view === "builder" ? "tab active" : "tab"}
            onClick={() => setView("builder")}
          >
            Builder
          </button>
          <button
            type="button"
            className={view === "tutorial" ? "tab active" : "tab"}
            onClick={() => setView("tutorial")}
          >
            Tutorial
          </button>
        </nav>
        {view === "builder" && (
        <div className="controls">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (selectedId) setProfileId(selectedId);
            }}
          >
            <label htmlFor="load-profile">Load an existing profile</label>
            <select
              id="load-profile"
              value={selectedId}
              onChange={(event) => setSelectedId(event.target.value)}
            >
              <option value="" disabled>
                {profiles.length ? "Select a profile…" : "No profiles yet"}
              </option>
              {profiles.map((p) => (
                <option key={p.profile_id} value={p.profile_id}>
                  {p.label === p.profile_id ? p.label : `${p.label} — ${p.profile_id}`}
                </option>
              ))}
            </select>
            <button type="submit" disabled={!selectedId}>
              Load
            </button>
          </form>
          <button type="button" onClick={clearEverything}>
            Clear everything
          </button>
        </div>
        )}
        {view === "builder" && profileId && (
          <p className="muted">
            Active profile: <strong>{profileId}</strong>
          </p>
        )}
      </header>
      {view === "tutorial" ? (
        <TutorialPage />
      ) : (
      <main>
        <SourcesPanel key={`sources-${sessionKey}`} onIngested={activateProfile} />
        <ProfilePanel key={`profile-${downstreamKey}`} profileId={profileId} />
        <TailorPanel key={`tailor-${downstreamKey}`} profileId={profileId} />
      </main>
      )}
    </div>
  );
}
