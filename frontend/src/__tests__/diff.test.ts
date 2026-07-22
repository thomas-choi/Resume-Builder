import { describe, expect, it } from "vitest";

import { diffExperiences, similarity } from "../lib/diff";
import { profileFixture, tailorFixture } from "./testUtils";

describe("similarity", () => {
  it("scores identical strings 1 and unrelated strings near 0", () => {
    expect(similarity("Built a backtester", "Built a backtester")).toBe(1);
    expect(similarity("Built a backtester", "")).toBe(0);
    expect(similarity("Python engineer", "zzz qqq")).toBeLessThan(0.2);
  });

  it("ignores case and surrounding whitespace", () => {
    expect(similarity("  Python  ", "python")).toBe(1);
  });

  it("scores a rewording above an unrelated claim", () => {
    const reworded = similarity(
      "Built a distributed trading backtester in Python",
      "Built a distributed backtesting engine in Python",
    );
    const unrelated = similarity(
      "Built a distributed trading backtester in Python",
      "Ran a team of 40 engineers",
    );
    expect(reworded).toBeGreaterThan(unrelated);
  });
});

describe("diffExperiences", () => {
  const profile = profileFixture();
  const result = tailorFixture();

  it("pairs each tailored bullet with its profile original", () => {
    const [diff] = diffExperiences(profile, result.tailored_cv, result.validation);
    expect(diff.company).toBe("Acme Corp");
    expect(diff.bullets[0]).toMatchObject({
      status: "unchanged",
      original: "Built a distributed trading backtester in Python",
    });
  });

  it("marks a bullet the validation gate flagged", () => {
    const [diff] = diffExperiences(profile, result.tailored_cv, result.validation);
    expect(diff.bullets[1].status).toBe("flagged");
  });

  it("marks an unflagged bullet with no profile origin as new", () => {
    const [diff] = diffExperiences(profile, result.tailored_cv, {
      passed: true,
      needs_review: false,
      flags: [],
    });
    expect(diff.bullets[1].status).toBe("new");
  });

  it("labels a close rewording as reworded, not new", () => {
    const cv = {
      ...result.tailored_cv,
      selected_experiences: [
        {
          ...result.tailored_cv.selected_experiences[0],
          bullets: ["Built a distributed trading backtester using Python"],
        },
      ],
    };
    const [diff] = diffExperiences(profile, cv, { passed: true, needs_review: false, flags: [] });
    expect(diff.bullets[0].status).toBe("reworded");
  });

  it("lists profile bullets the tailored CV left out", () => {
    const [diff] = diffExperiences(profile, result.tailored_cv, result.validation);
    expect(diff.dropped).toEqual(["Led migration of the data pipeline to PostgreSQL"]);
  });

  it("survives an experience that is not in the profile at all", () => {
    const cv = {
      ...result.tailored_cv,
      selected_experiences: [
        {
          ...result.tailored_cv.selected_experiences[0],
          company: "Nowhere Ltd",
        },
      ],
    };
    const [diff] = diffExperiences(profile, cv, result.validation);
    expect(diff.bullets[0].original).toBeNull();
    expect(diff.dropped).toEqual([]);
  });
});
