import type { ConflictStatus } from "../domain/types";

export type QueueFilter = "all" | "open" | "escalated" | "resolved";

export const QUEUE_FILTERS: { id: QueueFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "open", label: "Open" },
  { id: "escalated", label: "Escalated" },
  { id: "resolved", label: "Resolved" },
];

/**
 * "Open" = unresolved; "Escalated" is its own bucket (the senior-review
 * queue); everything else counts as resolved (accepted / preserved /
 * source-unreliable / closed).
 */
export function matchesQueueFilter(status: ConflictStatus, filter: QueueFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "open":
      return status === "unresolved";
    case "escalated":
      return status === "escalated";
    case "resolved":
      return status !== "unresolved" && status !== "escalated";
  }
}
