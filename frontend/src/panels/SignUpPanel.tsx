/** Sign-up screen: first name, last name, email → a verification challenge. */

import { useState } from "react";

import { signup } from "../lib/api";

interface Props {
  /** Handed the (email, method) once the challenge is sent. */
  onChallengeSent: (email: string, method: "code" | "link") => void;
  onSwitchToSignIn: () => void;
}

export function SignUpPanel({ onChallengeSent, onSwitchToSignIn }: Props) {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    // Validate all three client-side before posting — a blank field is the
    // user's mistake to catch here, not a round-trip.
    if (!firstName.trim() || !lastName.trim() || !email.trim()) {
      setError("First name, last name and email are all required.");
      return;
    }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      setError("Enter a valid email address.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const { method } = await signup({
        firstName: firstName.trim(),
        lastName: lastName.trim(),
        email: email.trim(),
      });
      onChallengeSent(email.trim(), method);
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
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>
        {busy ? "Sending…" : "Sign up"}
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
