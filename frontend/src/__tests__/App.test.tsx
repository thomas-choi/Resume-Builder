/** Session lifecycle: what "Clear everything" wipes, and what a new profile wipes. */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import * as api from "../lib/api";
import { profileFixture, renderWithClient, tailorFixture } from "./testUtils";

/** A finished tailoring run — no review pause, one downloadable document. */
const tailorResponse = tailorFixture({
  review_required: false,
  review: null,
  validation: { passed: true, needs_review: false, flags: [] },
  documents: [
    {
      kind: "cv",
      format: "docx",
      filename: "alice-cv.docx",
      size_bytes: 1234,
      url: "/document/t-1/cv.docx",
    },
  ],
});

/** Type a profile id into the header and load it. */
async function load(user: ReturnType<typeof userEvent.setup>, profileId: string) {
  const input = screen.getByLabelText("Load an existing profile");
  await user.clear(input);
  await user.type(input, profileId);
  await user.click(screen.getByRole("button", { name: "Load" }));
}

describe("App session lifecycle", () => {
  beforeEach(() => {
    vi.spyOn(api, "subscribeToIngest").mockImplementation(() => () => {});
    vi.spyOn(api, "getProfile").mockImplementation(async (profileId: string) => ({
      profile_id: profileId,
      version: 1,
      versions: [1],
      profile: profileFixture(),
    }));
  });
  afterEach(() => vi.restoreAllMocks());

  it("clears every panel and the query cache on 'Clear everything'", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderWithClient(<App />);

    await user.type(screen.getByLabelText(/Anything else/), "Some notes");
    await load(user, "alice");
    expect(await screen.findByText(/Alice Smith/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear everything" }));

    expect(screen.queryByText(/Active profile:/)).toBeNull();
    expect(screen.getByLabelText("Load an existing profile")).toHaveValue("");
    expect(screen.getByLabelText(/Anything else/)).toHaveValue("");
    // The cache was dropped, so the old profile cannot flash back on remount.
    expect(screen.queryByText(/Alice Smith/)).toBeNull();
  });

  it("changes nothing when the confirmation is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderWithClient(<App />);

    await user.type(screen.getByLabelText(/Anything else/), "Some notes");
    await load(user, "alice");
    expect(await screen.findByText(/Alice Smith/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear everything" }));

    expect(screen.getByText(/Active profile:/)).toHaveTextContent("alice");
    expect(screen.getByLabelText(/Anything else/)).toHaveValue("Some notes");
    expect(screen.getByText(/Alice Smith/)).toBeInTheDocument();
  });

  it("drops the previous profile's tailored CV when the active profile changes", async () => {
    vi.spyOn(api, "tailor").mockResolvedValue(tailorResponse);
    const user = userEvent.setup();
    renderWithClient(<App />);

    await load(user, "alice");
    await user.type(screen.getByLabelText("Job post"), "Backend engineer wanted");
    await user.click(screen.getByRole("button", { name: "Tailor CV" }));

    expect(await screen.findByText("Senior Backend Engineer")).toBeInTheDocument();
    // The download link carries the old tailor_id — the worst thing to leave up.
    expect(screen.getByText("alice-cv.docx")).toBeInTheDocument();

    await load(user, "bob");

    await waitFor(() => expect(screen.queryByText("Senior Backend Engineer")).toBeNull());
    expect(screen.queryByText("alice-cv.docx")).toBeNull();
    expect(screen.getByLabelText("Job post")).toHaveValue("");
  });
});
