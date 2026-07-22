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

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ProfilePanel } from "./panels/ProfilePanel";
import { SourcesPanel } from "./panels/SourcesPanel";
import { TailorPanel } from "./panels/TailorPanel";

export function App() {
  const queryClient = useQueryClient();
  const [profileId, setProfileId] = useState<string | null>(null);
  const [profileIdInput, setProfileIdInput] = useState("");
  const [sessionKey, setSessionKey] = useState(0);

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
    setProfileIdInput("");
    setSessionKey((key) => key + 1);
  }

  const downstreamKey = `${sessionKey}:${profileId ?? ""}`;

  return (
    <div className="app">
      <header>
        <h1>Resume Builder</h1>
        <div className="controls">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (profileIdInput.trim()) setProfileId(profileIdInput.trim());
            }}
          >
            <label htmlFor="load-profile">Load an existing profile</label>
            <input
              id="load-profile"
              value={profileIdInput}
              onChange={(event) => setProfileIdInput(event.target.value)}
              placeholder="profile id"
            />
            <button type="submit">Load</button>
          </form>
          <button type="button" onClick={clearEverything}>
            Clear everything
          </button>
        </div>
        {profileId && (
          <p className="muted">
            Active profile: <strong>{profileId}</strong>
          </p>
        )}
      </header>
      <main>
        <SourcesPanel key={`sources-${sessionKey}`} onIngested={setProfileId} />
        <ProfilePanel key={`profile-${downstreamKey}`} profileId={profileId} />
        <TailorPanel key={`tailor-${downstreamKey}`} profileId={profileId} />
      </main>
    </div>
  );
}
