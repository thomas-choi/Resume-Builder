/**
 * The authentication boundary (§14.12).
 *
 * Wraps the app: `GET /auth/me` decides whether to show the sign-up / sign-in
 * screens or the real panels. A mid-session `401` from *any* call drops straight
 * back to signed-out — distinguished from a network blip by status (Phase 6.c: a
 * failed refresh keeps loaded data on screen; a 401 is the one case where
 * erasing is correct).
 *
 * Auth is password-based (Phase 7.f): sign-up and sign-in both establish the
 * session cookie directly, so there is no intermediate verify step. A signed-in
 * account can change its password in place.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { getAuthMe, setUnauthorizedHandler, signout, UnauthorizedError } from "./lib/api";
import type { UserPublic } from "./lib/types";
import { ChangePasswordPanel } from "./panels/ChangePasswordPanel";
import { SignInPanel } from "./panels/SignInPanel";
import { SignUpPanel } from "./panels/SignUpPanel";

type Screen = "signin" | "signup";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [screen, setScreen] = useState<Screen>("signin");
  const [changingPassword, setChangingPassword] = useState(false);
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
      setChangingPassword(false);
      setScreen("signin");
    },
  });

  function onSignedIn(user: UserPublic) {
    // The cookie is set; seed the cache so the app renders without a round-trip.
    setSignedOut(false);
    queryClient.setQueryData(["auth", "me"], user);
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
          <div className="auth-bar-actions">
            {!changingPassword && (
              <button
                type="button"
                className="linklike"
                onClick={() => setChangingPassword(true)}
              >
                Change password
              </button>
            )}
            <button type="button" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
              Sign out
            </button>
          </div>
        </div>
        {changingPassword ? (
          <div className="auth-screen">
            <ChangePasswordPanel
              onDone={() => setChangingPassword(false)}
              onCancel={() => setChangingPassword(false)}
            />
          </div>
        ) : (
          children
        )}
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
      {screen === "signup" ? (
        <SignUpPanel
          onSignedIn={onSignedIn}
          onSwitchToSignIn={() => setScreen("signin")}
        />
      ) : (
        <SignInPanel
          onSignedIn={onSignedIn}
          onSwitchToSignUp={() => setScreen("signup")}
        />
      )}
    </div>
  );
}
