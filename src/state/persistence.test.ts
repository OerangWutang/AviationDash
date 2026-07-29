import { describe, expect, it } from "vitest";
import {
  parseSnapshot,
  toSnapshot,
  SNAPSHOT_VERSION,
  type SnapshotSource,
} from "./persistence";
import * as sample from "../data/sampleCase";

function source(): SnapshotSource {
  return {
    claims: new Map(sample.claims.map((c) => [c.id, c])),
    conflicts: new Map(sample.conflicts.map((c) => [c.id, c])),
    conflictOrder: sample.conflicts.map((c) => c.id),
    decisions: new Map(sample.decisions.map((d) => [d.id, d])),
    auditEvents: sample.auditEvents,
    reportSections: sample.reportSections,
    activeReviewerId: "rev-okafor",
  };
}

describe("case snapshot persistence", () => {
  it("round-trips through JSON intact", () => {
    const snapshot = toSnapshot(source());
    const restored = parseSnapshot(JSON.stringify(snapshot));
    expect(restored).not.toBeNull();
    expect(restored).toEqual(snapshot);
    expect(restored!.claims).toHaveLength(sample.claims.length);
    expect(restored!.conflictOrder[0]).toBe("cf-1");
  });

  it("rejects corrupt JSON", () => {
    expect(parseSnapshot("{not json")).toBeNull();
    expect(parseSnapshot("null")).toBeNull();
    expect(parseSnapshot('"a string"')).toBeNull();
  });

  it("rejects a snapshot from a future schema version", () => {
    const snapshot = { ...toSnapshot(source()), version: SNAPSHOT_VERSION + 1 };
    expect(parseSnapshot(JSON.stringify(snapshot))).toBeNull();
  });

  it("migrates a v1 snapshot losslessly with reportSections deferred to the seed", () => {
    const { reportSections: _sections, ...v1Fields } = toSnapshot(source());
    const v1 = { ...v1Fields, version: 1 };
    const restored = parseSnapshot(JSON.stringify(v1));
    expect(restored).not.toBeNull();
    expect(restored!.version).toBe(SNAPSHOT_VERSION);
    expect(restored!.reportSections).toBeNull();
    expect(restored!.claims).toHaveLength(sample.claims.length);
  });

  it("rejects a v2 snapshot with a broken reportSections field", () => {
    const snapshot = { ...toSnapshot(source()), reportSections: "nope" };
    expect(parseSnapshot(JSON.stringify(snapshot))).toBeNull();
  });

  it("rejects structurally broken snapshots", () => {
    const good = toSnapshot(source());
    expect(parseSnapshot(JSON.stringify({ ...good, claims: "nope" }))).toBeNull();
    expect(parseSnapshot(JSON.stringify({ ...good, claims: [] }))).toBeNull();
    expect(parseSnapshot(JSON.stringify({ ...good, conflictOrder: [] }))).toBeNull();
    expect(
      parseSnapshot(JSON.stringify({ ...good, activeReviewerId: 7 })),
    ).toBeNull();
  });
});
