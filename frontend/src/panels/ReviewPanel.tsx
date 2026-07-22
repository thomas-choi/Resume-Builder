/**
 * The human-review checkpoint: approve or remove each flagged claim.
 *
 * Defaults matter here. Every item starts *unapproved*, mirroring the server
 * (`review.apply_decision` treats silence as removal), so a reviewer who
 * submits without reading has dropped the untraceable claims rather than
 * shipped them.
 */

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { resumeTailor } from "../lib/api";
import type { ReviewRequest, TailorResponse } from "../lib/types";

interface Props {
  review: ReviewRequest;
  onResumed: (result: TailorResponse) => void;
}

export function ReviewPanel({ review, onResumed }: Props) {
  const [approvals, setApprovals] = useState<Record<string, boolean>>({});
  const [notes, setNotes] = useState("");

  const resume = useMutation<TailorResponse, Error>({
    mutationFn: () =>
      resumeTailor(review.tailor_id, { approvals, approve_all: false, notes }),
    onSuccess: onResumed,
  });

  const approvedCount = Object.values(approvals).filter(Boolean).length;
  const removedCount = review.items.length - approvedCount;

  return (
    <div className="review" aria-labelledby="review-heading">
      <h3 id="review-heading">Review before rendering</h3>
      <p>
        {review.items.length} claim(s) could not be traced back to your profile. Nothing is
        rendered until you decide. Anything you do not keep is removed from the CV — the
        rest still renders.
      </p>
      {review.brief && <blockquote className="brief">{review.brief}</blockquote>}

      <ul className="flags">
        {review.items.map((item) => {
          const approved = approvals[item.id] ?? false;
          return (
            <li key={item.id} className={approved ? "kept" : "removed"}>
              <p className="claim">
                <span className="kind">{item.kind}</span> “{item.item}”
              </p>
              <p className="muted">{item.reason}</p>
              {item.closest_profile_text && (
                <p className="closest">
                  Closest in your profile: “{item.closest_profile_text}”
                  {item.source && <span className="muted"> ({item.source})</span>}
                </p>
              )}
              <div role="group" aria-label={`Decision for ${item.item}`}>
                <button
                  type="button"
                  aria-pressed={approved}
                  onClick={() => setApprovals({ ...approvals, [item.id]: true })}
                >
                  Keep
                </button>
                <button
                  type="button"
                  aria-pressed={!approved}
                  onClick={() => setApprovals({ ...approvals, [item.id]: false })}
                >
                  Remove
                </button>
              </div>
            </li>
          );
        })}
      </ul>

      <label htmlFor="review-notes">Notes (optional)</label>
      <textarea
        id="review-notes"
        rows={2}
        value={notes}
        onChange={(event) => setNotes(event.target.value)}
      />

      <button type="button" disabled={resume.isPending} onClick={() => resume.mutate()}>
        {resume.isPending
          ? "Rendering…"
          : `Approve ${approvedCount}, remove ${removedCount}, and render`}
      </button>
      {resume.isError && <p role="alert">Could not resume: {resume.error.message}</p>}
    </div>
  );
}
