import { describe, expect, it, vi } from "vitest";

import { validateNewMatter } from "./matters";

const valid = {
  name: "Matter",
  aircraft: "Aircraft",
  accidentDate: "2026-07-30",
  location: "Amsterdam",
  matterType: "wrongful_death",
  docketRef: "DCA26MA001",
};

describe("matter intake", () => {
  it("rejects future accident dates before submission", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-31T12:00:00Z"));
    try {
      expect(
        validateNewMatter({ ...valid, accidentDate: "2026-08-01" }),
      ).toMatch(/must not be in the future/i);
      expect(validateNewMatter(valid)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
