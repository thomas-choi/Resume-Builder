/** Sign-in screen: email only (the email is the user-id) → a challenge. */

import { useState } from "react";

import { signin } from "../lib/api";

interface Props {
  onChallengeSent: (email: string, method: "code" | "link") => void;
  onSwitchToSignUp: () => void;
}

export function SignInPanel({ onChallengeSent, onSwitchToSignUp }: Props) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim()) {
      setError("Enter your email address.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const { method } = await signin(email.trim());
      // The API returns an identical 202 for every branch (unknown address,
      // unverified, verified), so the screen must too — no confirmation copy
      // that would reveal whether the address has an account.
      onChallengeSent(email.trim(), method);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="auth-panel" onSubmit={submit}>
      <h2>Sign in</h2>
      <label htmlFor="signin-email">Email</label>
      <input
        id="signin-email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? "Sending…" : "Send verification"}
      </button>
      <p className="muted">
        No account yet?{" "}
        <button type="button" className="linklike" onClick={onSwitchToSignUp}>
          Sign up
        </button>
      </p>
    </form>
  );
}
