/**
 * Three-panel review UI (design doc §10): sources → profile → tailor.
 *
 * The panels share one piece of state, the active `profile_id`: ingestion
 * produces it, the profile panel edits that profile, and the tailor panel
 * tailors from it.
 */

import { useState } from "react";

import { ProfilePanel } from "./panels/ProfilePanel";
import { SourcesPanel } from "./panels/SourcesPanel";
import { TailorPanel } from "./panels/TailorPanel";

export function App() {
  const [profileId, setProfileId] = useState<string | null>(null);
  const [profileIdInput, setProfileIdInput] = useState("");

  return (
    <div className="app">
      <header>
        <h1>Resume Builder</h1>
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
        {profileId && (
          <p className="muted">
            Active profile: <strong>{profileId}</strong>
          </p>
        )}
      </header>
      <main>
        <SourcesPanel onIngested={setProfileId} />
        <ProfilePanel profileId={profileId} />
        <TailorPanel profileId={profileId} />
      </main>
    </div>
  );
}
