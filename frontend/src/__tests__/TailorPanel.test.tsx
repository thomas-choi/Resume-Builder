/** Tailor panel: the diff view, the review handoff, and the download list. */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TailorPanel } from "../panels/TailorPanel";
import * as api from "../lib/api";
import {
  profileFixture,
  projectFixture,
  renderWithClient,
  tailorFixture,
} from "./testUtils";

describe("TailorPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "getProfile").mockResolvedValue({
      profile_id: "alice",
      version: 1,
      versions: [1],
      profile: profileFixture(),
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("asks for a profile before it will tailor anything", () => {
    renderWithClient(<TailorPanel profileId={null} />);
    expect(screen.getByText(/Build or load a profile first/)).toBeInTheDocument();
  });

  it("keeps the tailor button disabled until a job post is pasted", async () => {
    const user = userEvent.setup();
    renderWithClient(<TailorPanel profileId="alice" />);
    const button = screen.getByRole("button", { name: "Tailor CV" });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText("Job post"), "We need a backend engineer");
    expect(button).toBeEnabled();
  });

  it("diffs tailored bullets against the profile and marks flagged ones", async () => {
    vi.spyOn(api, "tailor").mockResolvedValue(tailorFixture());
    const user = userEvent.setup();
    renderWithClient(<TailorPanel profileId="alice" />);

    await user.type(screen.getByLabelText("Job post"), "Backend engineer");
    await user.click(screen.getByRole("button", { name: "Tailor CV" }));

    expect(await screen.findByText("Senior Backend Engineer")).toBeInTheDocument();
    // Both sides of the diff are on screen, with the flagged bullet labelled...
    expect(
      screen.getAllByText("Built a distributed trading backtester in Python").length,
    ).toBeGreaterThan(1);
    expect(screen.getByText("flagged")).toBeInTheDocument();
    // ...and the profile bullet the tailored CV left out is still shown.
    expect(screen.getByText("— left out —")).toBeInTheDocument();
  });

  it("shows the projects that will be written into the document", async () => {
    // The .docx renders selected_projects between experience and skills; a
    // reviewer must see them before approving it.
    vi.spyOn(api, "getProfile").mockResolvedValue({
      profile_id: "alice",
      version: 1,
      versions: [1],
      profile: profileFixture({
        projects: [projectFixture(), projectFixture({ name: "playbook" })],
      }),
    });
    const result = tailorFixture({ review_required: false, review: null });
    vi.spyOn(api, "tailor").mockResolvedValue({
      ...result,
      tailored_cv: { ...result.tailored_cv, selected_projects: [projectFixture()] },
    });
    const user = userEvent.setup();
    renderWithClient(<TailorPanel profileId="alice" />);

    await user.type(screen.getByLabelText("Job post"), "Backend engineer");
    await user.click(screen.getByRole("button", { name: "Tailor CV" }));

    const list = await screen.findByRole("list", { name: "Selected projects" });
    expect(within(list).getByRole("link", { name: "myFinData" })).toHaveAttribute(
      "href",
      "https://github.com/alice/myFinData",
    );
    expect(within(list).getByText("Python, C++")).toBeInTheDocument();
    expect(screen.getByText(/Left out: playbook/)).toBeInTheDocument();
  });

  it("marks a project the validation gate could not trace", async () => {
    const result = tailorFixture({ review_required: false, review: null });
    vi.spyOn(api, "tailor").mockResolvedValue({
      ...result,
      tailored_cv: {
        ...result.tailored_cv,
        selected_projects: [projectFixture({ name: "Invented Project" })],
      },
      validation: {
        passed: false,
        needs_review: true,
        flags: [
          {
            item: "Invented Project",
            kind: "project",
            reason: "Project not present in the career profile",
            similarity: null,
          },
        ],
      },
    });
    const user = userEvent.setup();
    renderWithClient(<TailorPanel profileId="alice" />);

    await user.type(screen.getByLabelText("Job post"), "Backend engineer");
    await user.click(screen.getByRole("button", { name: "Tailor CV" }));

    const list = await screen.findByRole("list", { name: "Selected projects" });
    expect(within(list).getByText("flagged")).toBeInTheDocument();
  });

  it("hands a run that needs review to the review panel", async () => {
    vi.spyOn(api, "tailor").mockResolvedValue(tailorFixture());
    const user = userEvent.setup();
    renderWithClient(<TailorPanel profileId="alice" />);

    await user.type(screen.getByLabelText("Job post"), "Backend engineer");
    await user.click(screen.getByRole("button", { name: "Tailor CV" }));

    expect(await screen.findByText("Review before rendering")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /cv.docx/ })).not.toBeInTheDocument();
  });

  it("shows download links once a clean run has rendered", async () => {
    vi.spyOn(api, "tailor").mockResolvedValue(
      tailorFixture({
        review_required: false,
        review: null,
        validation: { passed: true, needs_review: false, flags: [] },
        documents: [
          {
            kind: "cv",
            format: "docx",
            filename: "cv.docx",
            size_bytes: 4096,
            url: "/document/t-1?kind=cv&format=docx",
          },
        ],
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<TailorPanel profileId="alice" />);

    await user.type(screen.getByLabelText("Job post"), "Backend engineer");
    await user.click(screen.getByRole("button", { name: "Tailor CV" }));

    const link = await screen.findByRole("link", { name: "cv.docx" });
    expect(link).toHaveAttribute("href", "/document/t-1?kind=cv&format=docx");
    expect(screen.getByText(/Every claim traces back/)).toBeInTheDocument();
    expect(screen.queryByText("Review before rendering")).not.toBeInTheDocument();
  });

  it("explains a skipped render", async () => {
    vi.spyOn(api, "tailor").mockResolvedValue(
      tailorFixture({
        review_required: false,
        review: null,
        render_skipped: "rendering not requested",
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<TailorPanel profileId="alice" />);

    await user.type(screen.getByLabelText("Job post"), "Backend engineer");
    await user.click(screen.getByRole("button", { name: "Tailor CV" }));

    expect(await screen.findByText(/Not rendered: rendering not requested/)).toBeInTheDocument();
  });

  it("surfaces a failed tailoring call", async () => {
    vi.spyOn(api, "tailor").mockRejectedValue(new Error("profile alice not found"));
    const user = userEvent.setup();
    renderWithClient(<TailorPanel profileId="alice" />);

    await user.type(screen.getByLabelText("Job post"), "Backend engineer");
    await user.click(screen.getByRole("button", { name: "Tailor CV" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("profile alice not found"),
    );
  });
});
