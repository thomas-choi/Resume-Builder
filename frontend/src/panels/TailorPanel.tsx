/** Panel 3 — paste a job post, diff the tailored CV, review flags, download. */

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getProfile, tailor } from "../lib/api";
import { diffExperiences } from "../lib/diff";
import type { ProfileResponse, TailorResponse } from "../lib/types";
import { ReviewPanel } from "./ReviewPanel";

interface Props {
  profileId: string | null;
}

export function TailorPanel({ profileId }: Props) {
  const [jobPost, setJobPost] = useState("");
  const [render, setRender] = useState(true);
  const [coverLetter, setCoverLetter] = useState(false);
  const [result, setResult] = useState<TailorResponse | null>(null);

  // The diff is against the same profile the server tailored from.
  const profileQuery = useQuery<ProfileResponse>({
    queryKey: ["profile", profileId],
    queryFn: () => getProfile(profileId as string),
    enabled: Boolean(profileId),
  });

  const run = useMutation<TailorResponse, Error>({
    mutationFn: () =>
      tailor({ profileId: profileId as string, jobPost, render, coverLetter }),
    onSuccess: setResult,
  });

  if (!profileId) {
    return (
      <section className="panel" aria-labelledby="tailor-heading">
        <h2 id="tailor-heading">3 · Tailor</h2>
        <p className="muted">Build or load a profile first.</p>
      </section>
    );
  }

  const diffs =
    result && profileQuery.data
      ? diffExperiences(profileQuery.data.profile, result.tailored_cv, result.validation)
      : [];

  return (
    <section className="panel" aria-labelledby="tailor-heading">
      <h2 id="tailor-heading">3 · Tailor</h2>

      <label htmlFor="job-post">Job post</label>
      <textarea
        id="job-post"
        rows={8}
        value={jobPost}
        onChange={(event) => setJobPost(event.target.value)}
        placeholder="Paste the posting here"
      />
      <label>
        <input
          type="checkbox"
          checked={render}
          onChange={(event) => setRender(event.target.checked)}
        />
        Render documents
      </label>
      <label>
        <input
          type="checkbox"
          checked={coverLetter}
          onChange={(event) => setCoverLetter(event.target.checked)}
        />
        Also write a cover letter
      </label>
      <button
        type="button"
        disabled={!jobPost.trim() || run.isPending}
        onClick={() => run.mutate()}
      >
        {run.isPending ? "Tailoring…" : "Tailor CV"}
      </button>
      {run.isError && <p role="alert">Tailoring failed: {run.error.message}</p>}

      {result && (
        <>
          <h3>{result.tailored_cv.headline}</h3>
          <p>{result.tailored_cv.summary}</p>

          <h4>Bullets — profile vs. tailored</h4>
          {diffs.map((diff) => (
            <div key={diff.key} className="diff">
              <p>
                <strong>{diff.title}</strong> · {diff.company}
              </p>
              <table>
                <thead>
                  <tr>
                    <th scope="col">From your profile</th>
                    <th scope="col">On the tailored CV</th>
                  </tr>
                </thead>
                <tbody>
                  {diff.bullets.map((bullet, index) => (
                    <tr key={index} className={`bullet ${bullet.status}`}>
                      <td>{bullet.original ?? <span className="muted">— nothing close —</span>}</td>
                      <td>
                        {bullet.tailored} <span className="status">{bullet.status}</span>
                      </td>
                    </tr>
                  ))}
                  {diff.dropped.map((bullet, index) => (
                    <tr key={`dropped-${index}`} className="bullet dropped">
                      <td>{bullet}</td>
                      <td className="muted">— left out —</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}

          <h4>Skills</h4>
          <p>{result.tailored_cv.highlighted_skills.join(", ") || "—"}</p>

          {result.review_required && result.review ? (
            <ReviewPanel review={result.review} onResumed={setResult} />
          ) : (
            <p className={result.validation.passed ? "ok" : "warn"}>
              {result.validation.passed
                ? "Every claim traces back to your profile."
                : `${result.validation.flags.length} claim(s) were accepted at review.`}
            </p>
          )}

          {result.render_skipped && <p className="warn">Not rendered: {result.render_skipped}</p>}
          {result.documents.length > 0 && (
            <>
              <h4>Documents</h4>
              <ul className="documents">
                {result.documents.map((document) => (
                  <li key={`${document.kind}-${document.format}`}>
                    <a href={document.url} download>
                      {document.filename}
                    </a>{" "}
                    <span className="muted">({document.size_bytes} bytes)</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}
