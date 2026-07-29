/**
 * Export the sample-case seed data as JSON for the Python backend.
 *
 * The TS module `src/data/sampleCase.ts` is the single source of truth for
 * seed data; the backend loads the generated JSON rather than maintaining a
 * hand-ported copy. Re-run after changing the sample case:
 *
 *   npx tsx scripts/export-seed.ts
 */
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import * as sample from "../src/data/sampleCase";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const outPath = join(root, "server", "src", "atlas_argus", "db", "seed_data.json");

const payload = {
  caseFile: sample.caseFile,
  reviewers: sample.reviewers,
  sources: sample.sources,
  claims: sample.claims,
  conflicts: sample.conflicts,
  decisions: sample.decisions,
  auditEvents: sample.auditEvents,
  reportSections: sample.reportSections,
};

writeFileSync(outPath, JSON.stringify(payload, null, 2) + "\n");
console.log(
  `wrote ${outPath}: ${payload.sources.length} sources, ${payload.claims.length} claims, ` +
    `${payload.conflicts.length} conflicts, ${payload.auditEvents.length} audit events`,
);
