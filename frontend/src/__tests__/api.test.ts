/** What the fetch wrappers actually put on the wire. */

import { afterEach, describe, expect, it, vi } from "vitest";

import { ingest } from "../lib/api";

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
