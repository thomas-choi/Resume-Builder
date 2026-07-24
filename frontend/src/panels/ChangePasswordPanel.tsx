/** Change-password screen for a signed-in account: current → new → confirm. */

import { useState } from "react";

import { changePassword } from "../lib/api";
import { PASSWORD_RULE_TEXT, validatePassword } from "../lib/password";

interface Props {
  onDone: () => void;
  onCancel: () => void;
}

export function ChangePasswordPanel({ onDone, onCancel }: Props) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!current) {
      setError("Enter your current password.");
      return;
    }
    const ruleError = validatePassword(next);
    if (ruleError) {
      setError(ruleError);
      return;
    }
    if (next !== confirm) {
      setError("The two new passwords do not match.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await changePassword(current, next);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="auth-panel" onSubmit={submit}>
      <h2>Change password</h2>
      <label htmlFor="cp-current">Current password</label>
      <input
        id="cp-current"
        type="password"
        value={current}
        onChange={(e) => setCurrent(e.target.value)}
      />
      <label htmlFor="cp-new">New password</label>
      <input
        id="cp-new"
        type="password"
        value={next}
        onChange={(e) => setNext(e.target.value)}
      />
      <p className="muted hint">{PASSWORD_RULE_TEXT}</p>
      <label htmlFor="cp-confirm">Confirm new password</label>
      <input
        id="cp-confirm"
        type="password"
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
      />
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Change password"}
        </button>
        <button type="button" className="linklike" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
