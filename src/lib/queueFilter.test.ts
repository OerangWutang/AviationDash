import { describe, expect, it } from "vitest";
import type { ConflictStatus } from "../domain/types";
import { matchesQueueFilter } from "./queueFilter";

const ALL_STATUSES: ConflictStatus[] = [
  "unresolved",
  "accepted_claim_a",
  "accepted_claim_b",
  "preserved_both",
  "escalated",
  "source_unreliable",
  "closed",
];

describe("matchesQueueFilter", () => {
  it("buckets every status exactly once across open/escalated/resolved", () => {
    for (const status of ALL_STATUSES) {
      const buckets = (["open", "escalated", "resolved"] as const).filter((f) =>
        matchesQueueFilter(status, f),
      );
      expect(buckets, status).toHaveLength(1);
      expect(matchesQueueFilter(status, "all")).toBe(true);
    }
  });

  it("escalated is its own bucket — the senior-review queue", () => {
    expect(matchesQueueFilter("escalated", "escalated")).toBe(true);
    expect(matchesQueueFilter("escalated", "open")).toBe(false);
    expect(matchesQueueFilter("escalated", "resolved")).toBe(false);
  });
});
