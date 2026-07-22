/** Profile panel: editing a draft and resolving conflicts explicitly. */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfilePanel } from "../panels/ProfilePanel";
import * as api from "../lib/api";
import { profileFixture, renderWithClient } from "./testUtils";

describe("ProfilePanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "getProfile").mockResolvedValue({
      profile_id: "alice",
      version: 2,
      versions: [1, 2],
      profile: profileFixture(),
    });
    vi.spyOn(api, "putProfile").mockResolvedValue({ profile_id: "alice", version: 3 });
  });
  afterEach(() => vi.restoreAllMocks());

  it("prompts for a profile when none is active", () => {
    renderWithClient(<ProfilePanel profileId={null} />);
    expect(screen.getByText(/Ingest some sources/)).toBeInTheDocument();
  });

  it("loads the profile and counts unresolved conflicts", async () => {
    renderWithClient(<ProfilePanel profileId="alice" />);
    expect(await screen.findByDisplayValue("Senior Engineer")).toBeInTheDocument();
    expect(screen.getByLabelText("1 unresolved")).toBeInTheDocument();
    expect(screen.getByText(/CV and LinkedIn disagree on start date/)).toBeInTheDocument();
  });

  it("saves an edited profile as a new version", async () => {
    const user = userEvent.setup();
    renderWithClient(<ProfilePanel profileId="alice" />);

    const headline = await screen.findByLabelText("Headline");
    await user.clear(headline);
    await user.type(headline, "Staff Engineer");
    await user.click(screen.getByRole("button", { name: "Save as new version" }));

    await waitFor(() => expect(api.putProfile).toHaveBeenCalled());
    const [, saved] = vi.mocked(api.putProfile).mock.calls[0];
    expect(saved.headline).toBe("Staff Engineer");
    expect(await screen.findByText("Saved as v3.")).toBeInTheDocument();
  });

  it("records which conflicting value the person chose", async () => {
    const user = userEvent.setup();
    renderWithClient(<ProfilePanel profileId="alice" />);

    const choice = await screen.findByRole("button", { name: /2019/ });
    await user.click(choice);
    expect(choice).toHaveAttribute("aria-pressed", "true");
    // Resolving it clears the outstanding count — the disagreement itself stays.
    expect(screen.queryByLabelText("1 unresolved")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save as new version" }));
    await waitFor(() => expect(api.putProfile).toHaveBeenCalled());
    const [, saved] = vi.mocked(api.putProfile).mock.calls[0];
    expect(saved.conflicts[0].resolution).toBe("2019");
    expect(saved.conflicts[0].values).toEqual({
      "cv_docx:resume.docx": "2020",
      "linkedin:export.zip": "2019",
    });
  });

  it("lets a resolution be undone", async () => {
    const user = userEvent.setup();
    renderWithClient(<ProfilePanel profileId="alice" />);

    await user.click(await screen.findByRole("button", { name: /2019/ }));
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByLabelText("1 unresolved")).toBeInTheDocument();
  });

  it("reports a profile that could not be loaded", async () => {
    vi.spyOn(api, "getProfile").mockRejectedValue(new Error("profile nope not found"));
    renderWithClient(<ProfilePanel profileId="nope" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("profile nope not found");
  });
});
