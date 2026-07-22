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
  source_errors: [],
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

  it("accumulates files across separate picks instead of replacing them", async () => {
    vi.spyOn(api, "ingest").mockResolvedValue(ingestResponse);
    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);

    const picker = screen.getByLabelText(/CV files/);
    await user.upload(picker, new File(["a"], "CV.docx"));
    await user.upload(picker, new File(["b"], "CV.pdf"));

    const staged = screen.getByRole("list", { name: "Staged CV files" });
    expect(staged).toHaveTextContent("CV.docx");
    expect(staged).toHaveTextContent("CV.pdf");

    await user.click(screen.getByRole("button", { name: "Build profile" }));
    await waitFor(() => expect(api.ingest).toHaveBeenCalled());
    expect(
      vi.mocked(api.ingest).mock.calls[0][0].files.map((file) => file.name),
    ).toEqual(["CV.docx", "CV.pdf"]);
  });

  it("removes a staged file, and lets the same file be picked again", async () => {
    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);

    const picker = screen.getByLabelText(/CV files/);
    const file = new File(["a"], "CV.docx");
    await user.upload(picker, file);
    await user.click(screen.getByRole("button", { name: "Remove CV.docx" }));

    expect(screen.queryByRole("list", { name: "Staged CV files" })).toBeNull();
    expect(screen.getByRole("button", { name: "Build profile" })).toBeDisabled();

    // The input was cleared on read, so re-picking it fires `change` again.
    await user.upload(picker, file);
    expect(screen.getByRole("list", { name: "Staged CV files" })).toHaveTextContent(
      "CV.docx",
    );
  });

  it("stages LinkedIn exports the same way", async () => {
    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);

    const picker = screen.getByLabelText(/LinkedIn data export/);
    await user.upload(picker, new File(["a"], "Export.zip"));
    await user.upload(picker, new File(["b"], "Positions.csv"));

    const staged = screen.getByRole("list", { name: "Staged LinkedIn exports" });
    expect(staged).toHaveTextContent("Export.zip");
    expect(staged).toHaveTextContent("Positions.csv");
  });

  it("keeps the GitHub token masked, and omits it when blank", async () => {
    vi.spyOn(api, "ingest").mockResolvedValue(ingestResponse);
    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);

    const token = screen.getByLabelText("GitHub token (optional)");
    expect(token).toHaveAttribute("type", "password");

    await user.type(screen.getByLabelText("GitHub username"), "octocat");
    await user.click(screen.getByRole("button", { name: "Build profile" }));
    await waitFor(() => expect(api.ingest).toHaveBeenCalled());
    expect(vi.mocked(api.ingest).mock.calls[0][0].githubToken).toBe("");

    await user.type(token, "ghp-secret");
    await user.click(screen.getByRole("button", { name: "Build profile" }));
    await waitFor(() => expect(api.ingest).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.ingest).mock.calls[1][0].githubToken).toBe("ghp-secret");
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

  it("names every skipped repo on a partial success", async () => {
    // The bug this guards: a run that lost a whole source rendered as a clean
    // success, which is what made it expensive to find.
    vi.spyOn(api, "ingest").mockResolvedValue({
      ...ingestResponse,
      source_errors: [
        { source: "github:alice", repo: "alice/repo-4", reason: "no tool call" },
        { source: "github:alice", repo: null, reason: "provider exploded" },
      ],
    });
    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);
    await user.type(screen.getByLabelText("GitHub username"), "alice");
    await user.click(screen.getByRole("button", { name: "Build profile" }));

    const list = await screen.findByRole("list", { name: "Skipped items" });
    expect(list).toHaveTextContent("alice/repo-4");
    expect(list).toHaveTextContent("no tool call");
    // A whole-source failure falls back to naming the source.
    expect(list).toHaveTextContent("github:alice");
    expect(list).toHaveTextContent("provider exploded");
    // The success banner must not read as unqualified success.
    expect(await screen.findByText(/2 skipped/)).toBeInTheDocument();
  });

  it("reports nothing skipped on a clean run", async () => {
    vi.spyOn(api, "ingest").mockResolvedValue(ingestResponse);
    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);
    await user.type(screen.getByLabelText("GitHub username"), "alice");
    await user.click(screen.getByRole("button", { name: "Build profile" }));

    expect(await screen.findByText(/ready/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Skipped items" })).toBeNull();
  });

  it("shows skipped repos live while the run is still going", async () => {
    let warn: (message: string) => void = () => {};
    vi.spyOn(api, "subscribeToIngest").mockImplementation(
      (_jobId, _onNode, _onDone, onWarning) => {
        warn = onWarning ?? (() => {});
        return () => {};
      },
    );
    vi.spyOn(api, "ingest").mockImplementation(async () => {
      warn("alice/repo-4: no tool call");
      return ingestResponse;
    });

    const user = userEvent.setup();
    renderWithClient(<SourcesPanel onIngested={() => {}} />);
    await user.type(screen.getByLabelText("GitHub username"), "alice");
    await user.click(screen.getByRole("button", { name: "Build profile" }));

    expect(
      await screen.findByRole("list", { name: "Skipped so far" }),
    ).toHaveTextContent("alice/repo-4: no tool call");
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
