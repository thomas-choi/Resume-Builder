/**
 * Static help page (Phase 7.g): a short overview of Resume Builder plus the two
 * external steps people get stuck on — exporting LinkedIn data and creating a
 * GitHub personal access token. No API calls; pure content.
 */

export function TutorialPage() {
  return (
    <div className="tutorial">
      <section>
        <h2>How Resume Builder works</h2>
        <p>
          Resume Builder turns the career information you already have into a
          resume tailored to one specific job — without inventing anything you
          did not tell it. You move left to right through three panels:
        </p>
        <ol>
          <li>
            <strong>Sources.</strong> Add everything the tool should know about
            you: a CV (<code>.docx</code>/<code>.pdf</code>), a LinkedIn data
            export, a GitHub username, and any free-text notes. Click
            <em> Build profile</em> and it extracts a single structured career
            profile from all of them.
          </li>
          <li>
            <strong>Profile.</strong> Review what it found. Fix anything wrong
            and resolve any conflicts it flags (for example, two sources
            disagreeing on a start date). Save to keep your edits as a new
            version.
          </li>
          <li>
            <strong>Tailor.</strong> Paste a job post and click
            <em> Tailor</em>. It selects and reframes the most relevant
            experience for that job, then renders a <code>.docx</code>/PDF (and
            an optional cover letter). If it makes any claim it cannot trace back
            to your profile, the run pauses so you can approve or drop it before
            anything is written.
          </li>
        </ol>
        <p className="muted">
          Nothing is fabricated: every tailored claim is checked against your
          saved profile, and anything unsupported is held for your review.
        </p>
      </section>

      <section>
        <h2>How to download your LinkedIn export (<code>summary.zip</code>)</h2>
        <p>
          LinkedIn lets you download a copy of your own data as a ZIP archive.
          Add that ZIP as a source and Resume Builder reads your positions,
          education and skills straight from it.
        </p>
        <ol>
          <li>
            Sign in to LinkedIn and open{" "}
            <strong>Settings &amp; Privacy</strong> (click your photo →
            <em> Settings &amp; Privacy</em>).
          </li>
          <li>
            Go to <strong>Data Privacy</strong> → <strong>Get a copy of your
            data</strong>.
          </li>
          <li>
            Choose <strong>Download larger data archive</strong> (everything) —
            or pick specific categories if you prefer — and click
            <em> Request archive</em>. Re-enter your password if asked.
          </li>
          <li>
            LinkedIn emails you when the archive is ready (often within minutes,
            sometimes up to 24 hours). Open the email or return to the same
            page and click <strong>Download archive</strong>.
          </li>
          <li>
            You now have a ZIP file (commonly named something like
            <code> Basic_LinkedInDataExport_….zip</code>). Add that file as a
            LinkedIn source in the Sources panel — no need to unzip it.
          </li>
        </ol>
      </section>

      <section>
        <h2>How to create a GitHub personal access token</h2>
        <p>
          A token is <strong>optional</strong>. Without one, Resume Builder can
          still read a username's public repositories. Add a token to raise
          GitHub's rate limits, and — if it is <em>your</em> token for
          <em> your</em> username — to include your private repos and
          organization work.
        </p>
        <ol>
          <li>
            Sign in to GitHub and open{" "}
            <strong>Settings</strong> (click your photo →
            <em> Settings</em>).
          </li>
          <li>
            In the left sidebar, scroll to{" "}
            <strong>Developer settings</strong> → <strong>Personal access
            tokens</strong>.
          </li>
          <li>
            Choose <strong>Fine-grained tokens</strong> (recommended) →
            <em> Generate new token</em>. A classic token works too.
          </li>
          <li>
            Give it a name and a short expiry. For read-only repo access grant
            <strong> Repository access</strong> and the read-only
            <strong> Contents</strong> / <strong>Metadata</strong> permissions
            (classic: tick the <code>repo</code> scope for private repos, or no
            scope at all for public-only).
          </li>
          <li>
            Click <strong>Generate token</strong> and copy it immediately —
            GitHub shows it only once.
          </li>
          <li>
            Paste it into the <strong>GitHub token</strong> field in the Sources
            panel. It is sent only for that one run and is never stored in your
            browser.
          </li>
        </ol>
        <p className="muted">
          Treat a token like a password. If you ever paste it somewhere by
          mistake, revoke it from the same Personal access tokens page.
        </p>
      </section>
    </div>
  );
}
