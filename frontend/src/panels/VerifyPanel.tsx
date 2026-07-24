/** Verify screen — one component, two modes (§14.5/§14.6).
 *
 * - **code:** a 6-digit input; posts `{email, code}` with the remembered email
 *   so nothing is re-typed. On `410` (expired / attempts exhausted) it offers to
 *   send a fresh code.
 * - **link:** rendered when the hash route is `#/verify`; reads `token` from the
 *   fragment, clears it from the URL with `history.replaceState` (so the secret
 *   is not left in history), and posts `{token}`. On `410` it offers a new link.
 */

import { useEffect, useRef, useState } from "react";

import { ApiError, verify } from "../lib/api";
import type { UserPublic } from "../lib/types";

interface Props {
  mode: "code" | "link";
  /** The address the code was sent to (code mode); used to post without re-typing. */
  email: string | null;
  onVerified: (user: UserPublic) => void;
  /** Re-send a challenge to `email`; only offered after a 410. */
  onResend: () => void;
}

export function VerifyPanel({ mode, email, onVerified, onResend }: Props) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [busy, setBusy] = useState(false);
  const linkTried = useRef(false);

  function handleFailure(err: unknown) {
    if (err instanceof ApiError && err.status === 410) {
      setExpired(true);
      setError("That verification has expired.");
      return;
    }
    setError(err instanceof Error ? err.message : String(err));
  }

  // Link mode: read the token from the hash, clear the fragment, then verify —
  // once (a StrictMode double-invoke must not double-consume the token).
  useEffect(() => {
    if (mode !== "link" || linkTried.current) return;
    linkTried.current = true;
    const hash = window.location.hash; // e.g. "#/verify?token=abc"
    const query = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
    const token = new URLSearchParams(query).get("token");
    // Strip the fragment so the token does not linger in the address bar / history.
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    if (!token) {
      setError("This verification link is missing its token.");
      return;
    }
    setBusy(true);
    verify({ token })
      .then(onVerified)
      .catch(handleFailure)
      .finally(() => setBusy(false));
  }, [mode, onVerified]);

  async function submitCode(event: React.FormEvent) {
    event.preventDefault();
    if (!email) {
      setError("We lost track of which address to verify — please start again.");
      return;
    }
    if (!/^\d{6}$/.test(code.trim())) {
      setError("Enter the 6-digit code from your email.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      onVerified(await verify({ email, code: code.trim() }));
    } catch (err) {
      handleFailure(err);
    } finally {
      setBusy(false);
    }
  }

  if (mode === "link") {
    return (
      <div className="auth-panel">
        <h2>Verifying…</h2>
        {busy && <p className="muted">Checking your link.</p>}
        {error && <p className="error">{error}</p>}
        {expired && (
          <button type="button" onClick={onResend}>
            Send me a new link
          </button>
        )}
      </div>
    );
  }

  return (
    <form className="auth-panel" onSubmit={submitCode}>
      <h2>Enter your code</h2>
      <p className="muted">
        We emailed a 6-digit code{email ? ` to ${email}` : ""}. Enter it below.
      </p>
      <label htmlFor="verify-code">Verification code</label>
      <input
        id="verify-code"
        inputMode="numeric"
        maxLength={6}
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
      />
      {error && <p className="error">{error}</p>}
      {expired ? (
        <button type="button" onClick={onResend}>
          Send me a new code
        </button>
      ) : (
        <button type="submit" disabled={busy}>
          {busy ? "Verifying…" : "Verify"}
        </button>
      )}
    </form>
  );
}
