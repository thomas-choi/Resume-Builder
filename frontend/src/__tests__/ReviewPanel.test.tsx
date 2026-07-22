/** The flag-approval flow — the UI half of design doc §11's review checkpoint. */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewPanel } from "../panels/ReviewPanel";
import * as api from "../lib/api";
import { renderWithClient, reviewFixture, tailorFixture } from "./testUtils";

describe("ReviewPanel", () => {
  beforeEach(() => {
    vi.spyOn(api, "resumeTailor").mockResolvedValue(
      tailorFixture({ review_required: false, review: null }),
    );
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows each flagged claim with its reason and closest profile text", () => {
    renderWithClient(<ReviewPanel review={reviewFixture()} onResumed={() => {}} />);
    expect(screen.getByText(/Ran a team of 40 engineers/)).toBeInTheDocument();
    expect(
      screen.getByText(/No profile bullet mentions managing a team/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Led migration of the data pipeline to PostgreSQL/),
    ).toBeInTheDocument();
    expect(screen.getByText(/One claim could not be traced/)).toBeInTheDocument();
  });

  it("defaults every item to removed, so submitting unread drops the claims", async () => {
    const user = userEvent.setup();
    renderWithClient(<ReviewPanel review={reviewFixture()} onResumed={() => {}} />);

    expect(
      screen.getByRole("button", { name: /Approve 0, remove 2, and render/ }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Approve 0, remove 2/ }));

    await waitFor(() =>
      expect(api.resumeTailor).toHaveBeenCalledWith("t-1", {
        approvals: {},
        approve_all: false,
        notes: "",
      }),
    );
  });

  it("sends one approval per kept item", async () => {
    const user = userEvent.setup();
    renderWithClient(<ReviewPanel review={reviewFixture()} onResumed={() => {}} />);

    const [keepFirst] = screen.getAllByRole("button", { name: "Keep" });
    await user.click(keepFirst);
    expect(
      screen.getByRole("button", { name: /Approve 1, remove 1, and render/ }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Approve 1, remove 1/ }));
    await waitFor(() =>
      expect(api.resumeTailor).toHaveBeenCalledWith(
        "t-1",
        expect.objectContaining({ approvals: { "flag-0": true } }),
      ),
    );
  });

  it("lets a kept item be taken back", async () => {
    const user = userEvent.setup();
    renderWithClient(<ReviewPanel review={reviewFixture()} onResumed={() => {}} />);

    const [keepFirst] = screen.getAllByRole("button", { name: "Keep" });
    await user.click(keepFirst);
    const [removeFirst] = screen.getAllByRole("button", { name: "Remove" });
    await user.click(removeFirst);

    await user.click(screen.getByRole("button", { name: /Approve 0, remove 2/ }));
    await waitFor(() =>
      expect(api.resumeTailor).toHaveBeenCalledWith(
        "t-1",
        expect.objectContaining({ approvals: { "flag-0": false } }),
      ),
    );
  });

  it("passes the resumed result back to the caller", async () => {
    const user = userEvent.setup();
    const onResumed = vi.fn();
    renderWithClient(<ReviewPanel review={reviewFixture()} onResumed={onResumed} />);
    await user.click(screen.getByRole("button", { name: /and render/ }));
    await waitFor(() => expect(onResumed).toHaveBeenCalled());
    expect(onResumed.mock.calls[0][0].review_required).toBe(false);
  });

  it("reports a failed resume instead of pretending it rendered", async () => {
    vi.spyOn(api, "resumeTailor").mockRejectedValue(new Error("no review pending"));
    const user = userEvent.setup();
    renderWithClient(<ReviewPanel review={reviewFixture()} onResumed={() => {}} />);
    await user.click(screen.getByRole("button", { name: /and render/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("no review pending");
  });
});
