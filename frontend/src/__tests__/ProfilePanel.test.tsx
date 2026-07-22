/** Profile panel: editing a draft and resolving conflicts explicitly. */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfilePanel } from "../panels/ProfilePanel";
import * as api from "../lib/api";
import { profileFixture, projectFixture, renderWithClient } from "./testUtils";

describe("ProfilePanel", () => {
  /** Serve a specific profile from the mocked API for one test. */
  const serve = (profile: ReturnType<typeof profileFixture>) =>
    vi.spyOn(api, "getProfile").mockResolvedValue({
      profile_id: "alice",
      version: 2,
      versions: [1, 2],
      profile,
    });

  beforeEach(() => {
    serve(profileFixture());
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

  it("lists the projects a GitHub source contributed", async () => {
    serve(
      profileFixture({
        projects: [projectFixture(), projectFixture({ name: "playbook", url: null })],
      }),
    );
    renderWithClient(<ProfilePanel profileId="alice" />);

    const link = await screen.findByRole("link", { name: "myFinData" });
    expect(link).toHaveAttribute("href", "https://github.com/alice/myFinData");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getAllByText("Python, C++")).toHaveLength(2);
    // A repo with no URL is still listed — just not as a link.
    expect(screen.getByText("playbook")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "playbook" })).not.toBeInTheDocument();
    expect(screen.getAllByText("github:alice")).toHaveLength(2);
  });

  it("shows a sample of a long project list until asked for all of it", async () => {
    const projects = Array.from({ length: 12 }, (_, index) =>
      projectFixture({ name: `repo-${index}`, url: null }),
    );
    serve(profileFixture({ projects }));
    const user = userEvent.setup();
    renderWithClient(<ProfilePanel profileId="alice" />);

    const list = await screen.findByRole("list", { name: "Projects" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(10);
    expect(screen.queryByText("repo-11")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show all 12" }));
    expect(within(list).getAllByRole("listitem")).toHaveLength(12);
    expect(screen.getByText("repo-11")).toBeInTheDocument();
  });

  it("says so when the profile has no projects rather than showing nothing", async () => {
    renderWithClient(<ProfilePanel profileId="alice" />);
    expect(await screen.findByText(/No projects/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
  });

  it("renders education entries whatever keys they carry", async () => {
    serve(
      profileFixture({
        education: [
          {
            degree: "BSc",
            field: "Computer Science",
            school: "State University",
            location: "Albany, NY",
            year: "2014",
          },
          { institution: "Night School", honours: "distinction" },
        ],
      }),
    );
    renderWithClient(<ProfilePanel profileId="alice" />);

    expect(
      await screen.findByText(
        "BSc · Computer Science · State University · Albany, NY · 2014",
      ),
    ).toBeInTheDocument();
    // An unexpected key is shown as-is rather than dropped.
    expect(screen.getByText("Night School")).toBeInTheDocument();
    expect(screen.getByText("honours: distinction")).toBeInTheDocument();
  });

  it("lists certifications", async () => {
    serve(profileFixture({ certifications: ["AWS Solutions Architect"] }));
    renderWithClient(<ProfilePanel profileId="alice" />);
    expect(await screen.findByText("AWS Solutions Architect")).toBeInTheDocument();
  });

  it("reports a profile that could not be loaded", async () => {
    vi.spyOn(api, "getProfile").mockRejectedValue(new Error("profile nope not found"));
    renderWithClient(<ProfilePanel profileId="nope" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("profile nope not found");
  });
});
