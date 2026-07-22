/** What the fetch wrappers actually put on the wire. */

import { afterEach, describe, expect, it, vi } from "vitest";

import { getProfile, ingest } from "../lib/api";

function mockFetch() {
  const fetchMock = vi.fn(
    async (_path: string, _init: RequestInit) => new Response("{}", { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentForm(fetchMock: ReturnType<typeof mockFetch>): FormData {
  return fetchMock.mock.calls[0][1].body as FormData;
}

const baseInput = {
  files: [],
  linkedinExports: [],
  githubUsername: "octocat",
  githubToken: "",
  freeText: "",
  profileId: "",
  jobId: "job-1",
};

describe("ingest", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("omits the GitHub token when it is blank", async () => {
    const fetchMock = mockFetch();
    await ingest({ ...baseInput, githubToken: "   " });
    expect(sentForm(fetchMock).has("github_token")).toBe(false);
  });

  it("sends the GitHub token when one is given", async () => {
    const fetchMock = mockFetch();
    await ingest({ ...baseInput, githubToken: " ghp-secret " });
    expect(sentForm(fetchMock).get("github_token")).toBe("ghp-secret");
  });

  it("sends every staged file under the same field name", async () => {
    const fetchMock = mockFetch();
    await ingest({
      ...baseInput,
      files: [new File(["a"], "CV.docx"), new File(["b"], "CV.pdf")],
    });
    const names = sentForm(fetchMock)
      .getAll("cv")
      .map((entry) => (entry as File).name);
    expect(names).toEqual(["CV.docx", "CV.pdf"]);
  });
});

describe("transport failures", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("names the unreachable API instead of reporting 'Failed to fetch'", async () => {
    // `fetch` rejects with a bare TypeError on a transport failure — a message
    // that reads as a bug in this code rather than a connection that dropped.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(getProfile("alice")).rejects.toThrow(
      "Could not reach the API (/profile/alice) — is the server running?",
    );
  });

  it("still reports the server's reason for an HTTP error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "profile alice not found" }), {
            status: 404,
          }),
      ),
    );
    await expect(getProfile("alice")).rejects.toThrow("profile alice not found");
  });

  it("forwards React Query's abort signal so a cancelled request is discarded", async () => {
    const fetchMock = mockFetch();
    const controller = new AbortController();

    await getProfile("alice", controller.signal);

    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });
});
