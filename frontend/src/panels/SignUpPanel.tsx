/** Sign-up screen: name, email, and a password (typed twice) → signed in. */

import { useState } from "react";

import { signup } from "../lib/api";
import { PASSWORD_RULE_TEXT, validatePassword } from "../lib/password";
import type { UserPublic } from "../lib/types";

interface Props {
  /** Handed the account once sign-up succeeds (the session cookie is already set). */
  onSignedIn: (user: UserPublic) => void;
  onSwitchToSignIn: () => void;
}

export function SignUpPanel({ onSignedIn, onSwitchToSignIn }: Props) {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    // Catch blanks, the password rule and the mistype client-side before posting.
    if (!firstName.trim() || !lastName.trim() || !email.trim()) {
      setError("First name, last name and email are all required.");
      return;
    }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      setError("Enter a valid email address.");
      return;
    }
    const ruleError = validatePassword(password);
    if (ruleError) {
      setError(ruleError);
      return;
    }
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const user = await signup({
        firstName: firstName.trim(),
        lastName: lastName.trim(),
        email: email.trim(),
        password,
      });
      onSignedIn(user);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="auth-panel" onSubmit={submit}>
      <h2>Create your account</h2>
      <label htmlFor="signup-first">First name</label>
      <input
        id="signup-first"
        value={firstName}
        onChange={(e) => setFirstName(e.target.value)}
      />
      <label htmlFor="signup-last">Last name</label>
      <input
        id="signup-last"
        value={lastName}
        onChange={(e) => setLastName(e.target.value)}
      />
      <label htmlFor="signup-email">Email</label>
      <input
        id="signup-email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <label htmlFor="signup-password">Password</label>
      <input
        id="signup-password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <p className="muted hint">{PASSWORD_RULE_TEXT}</p>
      <label htmlFor="signup-confirm">Confirm password</label>
      <input
        id="signup-confirm"
        type="password"
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
      />
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? "Creating…" : "Sign up"}
      </button>
      <p className="muted">
        Already have an account?{" "}
        <button type="button" className="linklike" onClick={onSwitchToSignIn}>
          Sign in
        </button>
      </p>
    </form>
  );
}
