/** The auth boundary: signed-out screens, password flows, and the 401-vs-network split. */

import { screen } from "@testing-library/react";
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

  it("renders the app, change-password and sign-out when signed in", async () => {
    stubAuthFetch({ me: userFixture() });
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    expect(await screen.findByTestId("panels")).toBeInTheDocument();
    expect(screen.getByText(/Alice Smith/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change password" })).toBeInTheDocument();
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

  it("signs in with email + password and shows the app", async () => {
    const fetchMock = stubAuthFetch({
      me: 401,
      handlers: { "/auth/signin": () => jsonResponse(userFixture(), 200) },
    });
    const user = userEvent.setup();
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    await user.type(await screen.findByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Password"), "s3cret_pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const call = fetchMock.mock.calls.find((c) => String(c[0]).includes("/auth/signin"));
    expect(bodyOf(call!)).toEqual({ email: "alice@example.com", password: "s3cret_pw" });
    expect(await screen.findByTestId("panels")).toBeInTheDocument();
  });

  it("shows the server error on a bad password (uniform 401)", async () => {
    stubAuthFetch({
      me: 401,
      handlers: {
        "/auth/signin": () => jsonResponse({ detail: "Invalid email or password." }, 401),
      },
    });
    const user = userEvent.setup();
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    await user.type(await screen.findByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong_pass1");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText(/Invalid email or password/i)).toBeInTheDocument();
    expect(screen.queryByTestId("panels")).toBeNull();
  });

  it("enforces the password rule on sign-up before posting", async () => {
    const fetchMock = stubAuthFetch({ me: 401 });
    const user = userEvent.setup();
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    await user.click(await screen.findByRole("button", { name: "Sign up" }));
    await user.type(screen.getByLabelText("First name"), "Alice");
    await user.type(screen.getByLabelText("Last name"), "Smith");
    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    // No special char → rejected client-side, never posted.
    await user.type(screen.getByLabelText("Password"), "abcdefghij");
    await user.type(screen.getByLabelText("Confirm password"), "abcdefghij");
    await user.click(screen.getByRole("button", { name: "Sign up" }));

    expect(screen.getByText(/special character/i)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/auth/signup"))).toBe(
      false,
    );
  });

  it("catches a mistyped confirmation on sign-up", async () => {
    const fetchMock = stubAuthFetch({ me: 401 });
    const user = userEvent.setup();
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    await user.click(await screen.findByRole("button", { name: "Sign up" }));
    await user.type(screen.getByLabelText("First name"), "Alice");
    await user.type(screen.getByLabelText("Last name"), "Smith");
    await user.type(screen.getByLabelText("Email"), "alice@example.com");
    await user.type(screen.getByLabelText("Password"), "s3cret_pw");
    await user.type(screen.getByLabelText("Confirm password"), "s3cret_pX");
    await user.click(screen.getByRole("button", { name: "Sign up" }));

    expect(screen.getByText(/do not match/i)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("/auth/signup"))).toBe(
      false,
    );
  });

  it("changes password from the signed-in bar and returns to the app", async () => {
    const fetchMock = stubAuthFetch({
      me: userFixture(),
      handlers: { "/auth/change-password": () => new Response(null, { status: 204 }) },
    });
    const user = userEvent.setup();
    renderWithClient(<AuthGate>{PANELS}</AuthGate>);

    await user.click(await screen.findByRole("button", { name: "Change password" }));
    await user.type(screen.getByLabelText("Current password"), "s3cret_pw");
    await user.type(screen.getByLabelText("New password"), "an0ther-pw");
    await user.type(screen.getByLabelText("Confirm new password"), "an0ther-pw");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes("/auth/change-password"),
    );
    expect(bodyOf(call!)).toEqual({
      current_password: "s3cret_pw",
      new_password: "an0ther-pw",
    });
    // Back to the app.
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
