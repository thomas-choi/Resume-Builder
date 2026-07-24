/** The auth boundary: signed-out screens, verify flows, and the 401-vs-network split. */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "../AuthGate";
import { getProfile } from "../lib/api";
import { jsonResponse, renderWithClient, stubAuthFetch, userFixture } from "./testUtils";

const PANELS = <div data-testid="panels">the app</div>;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.location.hash = "";
});

function bodyOf(call: unknown[]): Record<string, unknown> {
  const init = call[1] as RequestInit;
  return JSON.parse(String(init.body));
}

describe("AuthGate", () => {
  it("shows the sign-in screen and no panels when signed out", async () => {
    stubAuthFetch({ me: 401 });
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByTestId("panels")).toBeNull();
  });

  it("renders the app and a sign-out button when signed in", async () => {
    stubAuthFetch({ me: userFixture() });
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    expect(await screen.findByTestId("panels")).toBeInTheDocument();
    expect(screen.getByText(/Alice Smith/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("validates all three sign-up fields before posting", async () => {
    const fetchMock = stubAuthFetch({ me: 401 });
    const user = userEvent.setup();
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    // Switch to the sign-up screen via its link, then submit it empty.
    await user.click(await screen.findByRole("button", { name: "Sign up" }));
    await user.click(screen.getByRole("button", { name: "Sign up" }));

    expect(screen.getByText(/all required/i)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/auth/signup"))).toBe(
      false,
    );
  });

  it("code mode: verifies with the remembered email and offers a new code on 410", async () => {
    let verifyStatus = 410;
    const fetchMock = stubAuthFetch({
      me: 401,
      handlers: {
        "/auth/signin": () => jsonResponse({ status: "sent", method: "code" }, 202),
        "/auth/verify": () =>
          verifyStatus === 410
            ? jsonResponse({ detail: "verification expired" }, 410)
            : jsonResponse(userFixture(), 200),
      },
    });
    const user = userEvent.setup();
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    await user.type(await screen.findByLabelText("Email"), "alice@example.com");
    await user.click(screen.getByRole("button", { name: "Send verification" }));

    // Now on the code screen. Enter a code and submit.
    await user.type(await screen.findByLabelText("Verification code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    // The verify call carried the remembered email + code — no re-typing.
    const verifyCall = fetchMock.mock.calls.find((c) => String(c[0]).includes("/auth/verify"));
    expect(bodyOf(verifyCall!)).toEqual({ email: "alice@example.com", code: "123456" });
    // A 410 offers a fresh code.
    expect(await screen.findByRole("button", { name: "Send me a new code" })).toBeInTheDocument();
  });

  it("link mode: posts the token from the hash and clears the fragment", async () => {
    window.location.hash = "#/verify?token=magic-abc";
    const fetchMock = stubAuthFetch({
      me: 401,
      handlers: { "/auth/verify": () => jsonResponse(userFixture(), 200) },
    });
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes("/auth/verify"));
      expect(call).toBeTruthy();
      expect(bodyOf(call!)).toEqual({ token: "magic-abc" });
    });
    // The token is scrubbed from the URL so it does not linger in history.
    expect(window.location.hash).toBe("");
    // Success hands off to the app.
    expect(await screen.findByTestId("panels")).toBeInTheDocument();
  });

  it("a mid-session 401 drops to sign-in; a network failure keeps the app", async () => {
    // A child that pokes a business endpoint on demand.
    function Poker() {
      return (
        <button type="button" onClick={() => getProfile("p1").catch(() => {})}>
          poke
        </button>
      );
    }
    let sessionMode: "live" | "401" | "network" = "network";
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(/^https?:\/\/[^/]+/, "");
      if (path === "/auth/me") {
        // Once the session has expired, the re-check 401s too — which is what
        // makes the gate settle on the sign-in screen rather than bounce back.
        return sessionMode === "401"
          ? jsonResponse({ detail: "session expired" }, 401)
          : jsonResponse(userFixture(), 200);
      }
      if (path.startsWith("/profile")) {
        if (sessionMode === "401") return jsonResponse({ detail: "session expired" }, 401);
        throw new TypeError("Failed to fetch"); // transport error → network branch
      }
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderWithClient(
      <AuthGate>
        <Poker />
      </AuthGate>,
    );

    // Signed in; a network failure must NOT sign us out (Phase 6.c).
    await screen.findByRole("button", { name: "poke" });
    await user.click(screen.getByRole("button", { name: "poke" }));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByRole("button", { name: "poke" })).toBeInTheDocument();

    // A real 401 does drop to the sign-in screen.
    sessionMode = "401";
    await user.click(screen.getByRole("button", { name: "poke" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});
