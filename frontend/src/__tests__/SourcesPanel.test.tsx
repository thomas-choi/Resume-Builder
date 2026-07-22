/** Sources panel: what gets uploaded, and the live SSE progress. */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SourcesPanel } from "../panels/SourcesPanel";
import * as api from "../lib/api";
import { profileFixture, renderWithClient } from "./testUtils";

const ingestResponse = {
  job_id: "ui-1",
  run_id: "ui-1",
  profile_id: "alice",
  version: 1,
  profile: profileFixture(),
};

describe("SourcesPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "subscribeToIngest").mockImplementation(() => () => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("will not submit with no source at all", async () => {
    renderWithClient(<SourcesPanel onIngested={() => {}} />);
    expect(screen.getByRole("button", { name: "Build profile" })).toBeDisabled();
  });

  it("enables submission once any one source is provided", async () => {
    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);
    await user.type(screen.getByLabelText("GitHub username"), "octocat");
    expect(screen.getByRole("button", { name: "Build profile" })).toBeEnabled();
  });

  it("sends every filled-in source and reports the new profile", async () => {
    vi.spyOn(api, "ingest").mockResolvedValue(ingestResponse);
    const onIngested = vi.fn();
    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={onIngested} />);

    await user.upload(
      screen.getByLabelText(/CV files/),
      new File(["cv"], "resume.docx", { type: "application/octet-stream" }),
    );
    await user.type(screen.getByLabelText("GitHub username"), "octocat");
    await user.type(screen.getByLabelText(/Anything else/), "Some notes");
    await user.type(screen.getByLabelText(/existing profile/), "alice");
    await user.click(screen.getByRole("button", { name: "Build profile" }));

    await waitFor(() => expect(api.ingest).toHaveBeenCalled());
    const input = vi.mocked(api.ingest).mock.calls[0][0];
    expect(input.files.map((file) => file.name)).toEqual(["resume.docx"]);
    expect(input.githubUsername).toBe("octocat");
    expect(input.freeText).toBe("Some notes");
    expect(input.profileId).toBe("alice");
    expect(input.jobId).toBeTruthy();

    expect(await screen.findByText(/ready/)).toBeInTheDocument();
    expect(onIngested).toHaveBeenCalledWith("alice");
  });

  it("subscribes to progress before posting, and lists the nodes it hears", async () => {
    let emit: (node: string) => void = () => {};
    vi.spyOn(api, "subscribeToIngest").mockImplementation((_jobId, onNode) => {
      emit = onNode;
      return () => {};
    });
    vi.spyOn(api, "ingest").mockImplementation(async () => {
      emit("extract_source");
      emit("synthesize_profile");
      return ingestResponse;
    });

    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);
    await user.type(screen.getByLabelText("GitHub username"), "octocat");
    await user.click(screen.getByRole("button", { name: "Build profile" }));

    const progress = await screen.findByRole("list", { name: "Ingestion progress" });
    await waitFor(() =>
      expect(progress).toHaveTextContent("Extracting facts per source"),
    );
    expect(progress).toHaveTextContent("Synthesizing the profile");
  });

  it("surfaces a rejected upload", async () => {
    vi.spyOn(api, "ingest").mockRejectedValue(
      new Error("unsupported CV file type: .txt"),
    );
    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);
    await user.type(screen.getByLabelText("GitHub username"), "octocat");
    await user.click(screen.getByRole("button", { name: "Build profile" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unsupported CV file type");
  });
});
