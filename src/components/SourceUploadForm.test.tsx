import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { postCaseSource } from "../api/client";
import type { SourceDocument } from "../domain/types";
import { SourceUploadForm } from "./SourceUploadForm";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return { ...original, postCaseSource: vi.fn() };
});

const uploadedSource = {
  id: "src-uploaded",
  caseId: "case-a",
  title: "Exhibit",
  type: "official_report",
  origin: "NTSB",
  custodian: "Records",
  docketRef: null,
  ingestedAt: "2026-07-29T00:00:00Z",
  sha256: "a".repeat(64),
  privilegeStatus: "public",
  custody: [],
} as SourceDocument;

describe("SourceUploadForm matter scope", () => {
  beforeEach(() => {
    vi.mocked(postCaseSource).mockReset();
    Object.defineProperty(File.prototype, "arrayBuffer", {
      configurable: true,
      value: vi.fn(async () => new TextEncoder().encode("%PDF-1.4").buffer),
    });
  });

  it("clears a selected document when the active matter changes", async () => {
    const user = userEvent.setup();
    const onUploaded = vi.fn();
    const view = render(
      <SourceUploadForm
        caseId="case-a"
        canUploadPrivileged
        onUploaded={onUploaded}
      />,
    );

    await user.upload(
      screen.getByLabelText("Document"),
      new File(["%PDF-1.4"], "matter-a.pdf", { type: "application/pdf" }),
    );
    await user.type(screen.getByLabelText("Origin"), "NTSB");
    await user.type(screen.getByLabelText("Custodian"), "Records");
    expect(screen.getByLabelText("Title")).toHaveValue("matter-a");

    view.rerender(
      <SourceUploadForm
        caseId="case-b"
        canUploadPrivileged
        onUploaded={onUploaded}
      />,
    );

    await waitFor(() => expect(screen.getByLabelText("Title")).toHaveValue(""));
    expect(screen.getByLabelText("Origin")).toHaveValue("");
    expect(screen.getByLabelText("Custodian")).toHaveValue("");
    expect(screen.getByLabelText("Document")).toHaveValue("");
  });

  it("ignores an upload response after the user switches matters", async () => {
    const user = userEvent.setup();
    const onUploaded = vi.fn();
    let resolveUpload:
      | ((value: Awaited<ReturnType<typeof postCaseSource>>) => void)
      | undefined;
    vi.mocked(postCaseSource).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveUpload = resolve;
        }),
    );
    const view = render(
      <SourceUploadForm
        caseId="case-a"
        canUploadPrivileged
        onUploaded={onUploaded}
      />,
    );

    await user.upload(
      screen.getByLabelText("Document"),
      new File(["%PDF-1.4"], "matter-a.pdf", { type: "application/pdf" }),
    );
    await user.type(screen.getByLabelText("Origin"), "NTSB");
    await user.type(screen.getByLabelText("Custodian"), "Records");
    await user.click(screen.getByRole("button", { name: "Upload document" }));
    await waitFor(() => expect(postCaseSource).toHaveBeenCalledWith(
      "case-a",
      expect.any(Object),
      { signal: expect.any(AbortSignal) },
    ));
    const signal = vi.mocked(postCaseSource).mock.calls[0][2]?.signal;
    expect(signal?.aborted).toBe(false);

    view.rerender(
      <SourceUploadForm
        caseId="case-b"
        canUploadPrivileged
        onUploaded={onUploaded}
      />,
    );
    expect(signal?.aborted).toBe(true);
    resolveUpload?.({
      source: uploadedSource,
      possibleDuplicateSourceIds: [],
      replayed: false,
    });

    await waitFor(() => expect(screen.getByLabelText("Title")).toHaveValue(""));
    expect(onUploaded).not.toHaveBeenCalled();
  });
});
