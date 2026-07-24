/** Sign-in screen: email + password → signed in. */

import { useState } from "react";

import { signin } from "../lib/api";
import type { UserPublic } from "../lib/types";

interface Props {
  /** Handed the account once sign-in succeeds (the session cookie is already set). */
  onSignedIn: (user: UserPublic) => void;
  onSwitchToSignUp: () => void;
}

export function SignInPanel({ onSignedIn, onSwitchToSignUp }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const user = await signin(email.trim(), password);
      onSignedIn(user);
    } catch (err) {
      // The API returns an identical 401 for an unknown address and a wrong
      // password, so the message stays uniform — it never reveals which it was.
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
      <label htmlFor="signin-password">Password</label>
      <input
        id="signin-password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
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
