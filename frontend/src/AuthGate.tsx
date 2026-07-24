/**
 * The authentication boundary (§14.12).
 *
 * Wraps the app: `GET /auth/me` decides whether to show the sign-up / sign-in /
 * verify screens or the real panels. A mid-session `401` from *any* call drops
 * straight back to signed-out — distinguished from a network blip by status
 * (Phase 6.c: a failed refresh keeps loaded data on screen; a 401 is the one
 * case where erasing is correct).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { getAuthMe, setUnauthorizedHandler, signin, signout, UnauthorizedError } from "./lib/api";
import type { UserPublic } from "./lib/types";
import { SignInPanel } from "./panels/SignInPanel";
import { SignUpPanel } from "./panels/SignUpPanel";
import { VerifyPanel } from "./panels/VerifyPanel";

type Screen = "signin" | "signup" | "verify-code" | "verify-link" | "check-inbox";

function initialScreen(): Screen {
  // A magic link opens the app at `#/verify?token=…`; go straight to the link
  // verifier, which reads and then scrubs the token from the URL.
  return window.location.hash.startsWith("#/verify") ? "verify-link" : "signin";
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [screen, setScreen] = useState<Screen>(initialScreen);
  const [pendingEmail, setPendingEmail] = useState<string | null>(null);
  // Set by the global 401 handler. It overrides a possibly-stale `me` so the
  // gate lands on the sign-in screen deterministically, without racing the
  // query cache — this is the "signed-out state" a 401 drops to (§14.12).
  const [signedOut, setSignedOut] = useState(false);

  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: ({ signal }) => getAuthMe(signal),
    retry: false,
  });

  // Any 401 from a business call: clear the loaded data and drop to signed-out.
  // A network failure never reaches this (it throws a transport error, not a
  // 401), so a failed *refresh* keeps its data on screen (Phase 6.c).
  useEffect(() => {
    setUnauthorizedHandler(() => {
      queryClient.clear();
      setSignedOut(true);
    });
    return () => setUnauthorizedHandler(null);
  }, [queryClient]);

  const signOut = useMutation({
    mutationFn: signout,
    onSuccess: () => {
      // Same remount mechanism as "Clear everything" (Phase 6.a): nothing the
      // previous identity loaded outlives the sign-out.
      queryClient.clear();
      setSignedOut(true);
      setScreen("signin");
    },
  });

  function onVerified(user: UserPublic) {
    // The cookie is set; seed the cache so the app renders without a round-trip.
    setSignedOut(false);
    queryClient.setQueryData(["auth", "me"], user);
  }

  function onChallengeSent(email: string, method: "code" | "link") {
    setPendingEmail(email);
    setScreen(method === "code" ? "verify-code" : "check-inbox");
  }

  async function resend() {
    if (!pendingEmail) return;
    const { method } = await signin(pendingEmail);
    setScreen(method === "code" ? "verify-code" : "check-inbox");
  }

  if (me.isPending && !signedOut) {
    return <div className="auth-screen">Loading…</div>;
  }

  if (me.isSuccess && !signedOut) {
    const user = me.data;
    return (
      <>
        <div className="auth-bar">
          <span>
            Signed in as <strong>{user.first_name} {user.last_name}</strong>
          </span>
          <button type="button" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
            Sign out
          </button>
        </div>
        {children}
      </>
    );
  }

  // Not signed in (a 401, or an error surfaced by getAuthMe). Anything that is
  // not an UnauthorizedError still lands here as "please sign in" — the app is
  // unusable without a session either way.
  if (me.error && !(me.error instanceof UnauthorizedError)) {
    // Surface a genuine outage rather than silently showing the sign-in form.
    return (
      <div className="auth-screen">
        <p className="error">Could not reach the server. {String(me.error)}</p>
        <button type="button" onClick={() => me.refetch()}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="auth-screen">
      {screen === "signup" && (
        <SignUpPanel
          onChallengeSent={onChallengeSent}
          onSwitchToSignIn={() => setScreen("signin")}
        />
      )}
      {screen === "signin" && (
        <SignInPanel
          onChallengeSent={onChallengeSent}
          onSwitchToSignUp={() => setScreen("signup")}
        />
      )}
      {(screen === "verify-code" || screen === "verify-link") && (
        <VerifyPanel
          mode={screen === "verify-link" ? "link" : "code"}
          email={pendingEmail}
          onVerified={onVerified}
          onResend={resend}
        />
      )}
      {screen === "check-inbox" && (
        <div className="auth-panel">
          <h2>Check your inbox</h2>
          <p className="muted">
            If that address has an account, a verification link is on its way.
            Open it to finish signing in.
          </p>
          <button type="button" onClick={() => setScreen("signin")}>
            Back to sign in
          </button>
        </div>
      )}
    </div>
  );
}
