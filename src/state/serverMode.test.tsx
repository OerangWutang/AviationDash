import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../App";
import { StoreProvider, useStore } from "./store";
import { applyDecision } from "../domain/decisions";
import { computeSectionImpact } from "../domain/report";
import type { CasePayload } from "../api/client";
import * as sample from "../data/sampleCase";

const OKAFOR = { id: "rev-okafor", name: "Miriam Okafor", role: "Senior Aviation Counsel" };
const SESSION_EXPIRES_AT = "2099-07-18T12:00:00Z";

function sessionInfo(
  overrides: Partial<{
    mustChangePassword: boolean;
    mfa: { enabled: boolean; verified: boolean };
    expiresAt: string;
  }> = {},
) {
  return {
    reviewer: OKAFOR,
    mustChangePassword: false,
    mfa: { enabled: true, verified: true },
    expiresAt: SESSION_EXPIRES_AT,
    ...overrides,
  };
}

function casePayload(): CasePayload {
  return {
    caseFile: sample.caseFile,
    caseMembership: {
      caseId: sample.caseFile.id,
      reviewerId: "rev-okafor",
      role: "Senior Aviation Counsel",
      isActive: true,
    },
    reviewers: sample.reviewers,
    sources: sample.sources,
    claims: sample.claims,
    conflicts: sample.conflicts.map((conflict, index) => ({
      ...conflict,
      version: index + 1,
    })),
    conflictOrder: sample.conflicts.map((c) => c.id),
    decisions: sample.decisions,
    auditEvents: [...sample.auditEvents].sort((a, b) => b.at.localeCompare(a.at)),
    reportSections: sample.reportSections.map((section, index) => ({
      ...section,
      version: index + 1,
    })),
  };
}

function casePayloadFor(caseId: string, name: string): CasePayload {
  const payload = casePayload();
  return {
    ...payload,
    caseFile: {
      ...payload.caseFile,
      id: caseId,
      name,
      docketRef: `DOCKET-${caseId.toUpperCase()}`,
    },
    caseMembership: { ...payload.caseMembership, caseId },
    sources: payload.sources.map((source) => ({ ...source, caseId })),
    claims: payload.claims.map((claim) => ({ ...claim, caseId })),
    conflicts: payload.conflicts.map((conflict) => ({ ...conflict, caseId })),
    auditEvents: payload.auditEvents.map((event) => ({ ...event, caseId })),
    reportSections: payload.reportSections.map((section) => ({ ...section, caseId })),
  };
}

function caseSummaries() {
  return [
    {
      caseFile: sample.caseFile,
      caseMembership: {
        caseId: sample.caseFile.id,
        reviewerId: "rev-okafor",
        role: "Senior Aviation Counsel",
        isActive: true,
      },
    },
  ];
}

/** Fabricate a realistic server response by running the same domain logic
 *  the backend mirrors. */
function decisionResponse() {
  const conflict = sample.conflicts.find((c) => c.id === "cf-1")!;
  const claimA = sample.claims.find((c) => c.id === conflict.claimAId)!;
  const claimB = sample.claims.find((c) => c.id === conflict.claimBId)!;
  let n = 0;
  const result = applyDecision(
    {
      conflict,
      claimA,
      claimB,
      conflictsById: new Map(sample.conflicts.map((c) => [c.id, c])),
      now: "2026-07-10T20:00:00Z",
      makeId: () => `srv-${++n}`,
    },
    {
      conflictId: "cf-1",
      decisionType: "preserve_both",
      selectedClaimId: null,
      reasoning: "Both accounts preserved pending expert reconciliation of the timeline.",
      reviewerName: "Miriam Okafor",
      reviewerRole: "Senior Aviation Counsel",
    },
  );
  return {
    conflict: { ...result.conflict, version: 2 },
    claims: result.claims,
    decision: result.decision,
    auditEvents: result.auditEvents,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

type Route = (
  url: string,
  init?: RequestInit,
) => Response | Promise<Response> | null;

const fetchMock = vi.fn();

/** Standard authenticated-server routing; `overrides` win when they return
 *  a Response. */
function routeFetch(...overrides: Route[]) {
  fetchMock.mockImplementation((url: string, init?: RequestInit) => {
    for (const route of overrides) {
      const hit = route(url, init);
      if (hit) return Promise.resolve(hit);
    }
    if (url.endsWith("/api/auth/session")) {
      return Promise.resolve(jsonResponse(sessionInfo()));
    }
    if (url.endsWith("/api/cases")) {
      return Promise.resolve(jsonResponse({ cases: caseSummaries() }));
    }
    if (url.endsWith(`/api/cases/${sample.caseFile.id}`)) {
      return Promise.resolve(jsonResponse(casePayload()));
    }
    if (url.endsWith("/api/case")) {
      return Promise.resolve(jsonResponse(casePayload()));
    }
    return Promise.resolve(jsonResponse({ detail: `unrouted: ${url}` }, 500));
  });
}

function renderApp() {
  return render(
    <StoreProvider>
      <App />
    </StoreProvider>,
  );
}

function SensitiveStateProbe() {
  const { state } = useStore();
  return (
    <output aria-label="Sensitive state probe">
      {`${state.caseFile.id}|${state.sources.size}|${state.auditEvents.length}`}
    </output>
  );
}

function renderAppWithProbe() {
  return render(
    <StoreProvider>
      <App />
      <SensitiveStateProbe />
    </StoreProvider>,
  );
}

let controlledStore: ReturnType<typeof useStore> | null = null;

function StoreControlProbe() {
  controlledStore = useStore();
  return null;
}

function currentStore(): ReturnType<typeof useStore> {
  if (controlledStore === null) throw new Error("Store control probe is not mounted.");
  return controlledStore;
}

function renderControlledApp() {
  return render(
    <StoreProvider>
      <App />
      <StoreControlProbe />
    </StoreProvider>,
  );
}

beforeEach(() => {
  vi.stubEnv("VITE_API_URL", "http://testhost:8100");
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  controlledStore = null;
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("server mode", () => {
  it("shows global administration without requesting a fallback case when no matter is assigned", async () => {
    routeFetch((url, init) => {
      if (url.endsWith("/api/cases") && init?.method === undefined) {
        return jsonResponse({ cases: [] });
      }
      if (url.endsWith("/api/admin/reviewers")) {
        return jsonResponse({ reviewers: [] });
      }
      if (url.endsWith("/api/admin/account-audit")) {
        return jsonResponse({ events: [] });
      }
      return null;
    });

    renderApp();
    expect(
      await screen.findByRole("heading", { name: "No matters assigned" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open a new matter" }),
    ).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/case")),
    ).toBe(false);
  });

  it("shows a non-administrative empty state to a non-Senior with no matters", async () => {
    routeFetch((url, init) => {
      if (url.endsWith("/api/auth/session")) {
        return jsonResponse({
          ...sessionInfo(),
          reviewer: { ...OKAFOR, role: "Claims Reviewer" },
        });
      }
      if (url.endsWith("/api/cases") && init?.method === undefined) {
        return jsonResponse({ cases: [] });
      }
      return null;
    });

    renderApp();
    expect(
      await screen.findByText(/must grant you access to a matter/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Open a new matter" }),
    ).not.toBeInTheDocument();
  });

  it("boots from the session + case endpoints and labels the mode honestly", async () => {
    routeFetch();
    renderApp();

    expect(screen.getByText(/Connecting to the case service/)).toBeInTheDocument();
    expect(await screen.findAllByText("Colgan Air Flight 3407")).not.toHaveLength(0);
    expect(screen.getByText("server")).toBeInTheDocument();
    // The signed-in identity replaces the "Acting as" picker (the name also
    // appears in the decision form's "Acting as" line).
    expect(screen.getAllByText("Miriam Okafor").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Active reviewer")).not.toBeInTheDocument();
  });

  it("uses a senior matter role for case actions without granting account admin", async () => {
    const accountReviewer = { ...OKAFOR, role: "Claims Reviewer" };
    routeFetch((url) => {
      if (url.endsWith("/api/auth/session")) {
        return jsonResponse({ ...sessionInfo(), reviewer: accountReviewer });
      }
      if (url.endsWith("/api/cases/case-3407")) {
        const payload = casePayload();
        return jsonResponse({
          ...payload,
          reportSections: payload.reportSections.map((section, index) =>
            index === 0 ? { ...section, approvalState: "draft" } : section,
          ),
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    expect(
      screen.getByText(
        /Account: Claims Reviewer · Matter: Senior Aviation Counsel/,
      ),
    ).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Case sections" });
    expect(
      within(nav).queryByRole("button", { name: /Reviewer admin/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Accept Claim A/ })).toBeEnabled();
    expect(screen.getByRole("region", { name: "Review decision" })).toHaveTextContent(
      /Acting as Miriam Okafor · Senior Aviation Counsel/,
    );

    await user.click(within(nav).getByRole("button", { name: /Reports/ }));
    expect(
      screen.getByRole("button", { name: "Approve for production" }),
    ).toBeEnabled();

    await user.click(within(nav).getByRole("button", { name: /Evidence packet/ }));
    expect(screen.getByRole("radio", { name: /Production packet/ })).toBeEnabled();
    expect(screen.getByRole("region", { name: "Packet options" })).toHaveTextContent(
      /Acting as Miriam Okafor · Senior Aviation Counsel/,
    );
  });

  it("uses a limited matter role for case actions while preserving account admin", async () => {
    routeFetch((url) => {
      if (url.endsWith("/api/cases/case-3407")) {
        const payload = casePayload();
        return jsonResponse({
          ...payload,
          caseMembership: { ...payload.caseMembership, role: "Claims Reviewer" },
          reportSections: payload.reportSections.map((section, index) =>
            index === 0 ? { ...section, approvalState: "draft" } : section,
          ),
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    expect(
      screen.getByText(
        /Account: Senior Aviation Counsel · Matter: Claims Reviewer/,
      ),
    ).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Case sections" });
    expect(
      within(nav).getByRole("button", { name: /Reviewer admin/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Accept Claim A/ })).toBeDisabled();
    expect(screen.getByRole("region", { name: "Review decision" })).toHaveTextContent(
      /Acting as Miriam Okafor · Claims Reviewer/,
    );

    await user.click(within(nav).getByRole("button", { name: /Reports/ }));
    expect(
      screen.getByRole("button", { name: "Approve for production" }),
    ).toBeDisabled();

    await user.click(within(nav).getByRole("button", { name: /Evidence packet/ }));
    expect(screen.getByRole("radio", { name: /Production packet/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Generate packet" })).toBeDisabled();
  });

  it("counts only approved, usable sections as production-exportable", async () => {
    const payload = casePayload();
    const claims = new Map(payload.claims.map((claim) => [claim.id, claim]));
    const conflicts = new Map(
      payload.conflicts.map((conflict) => [conflict.id, conflict]),
    );
    const usable = payload.reportSections.filter((section) => {
      const status = computeSectionImpact(section, claims, conflicts).status;
      return status === "eligible" || status === "eligible_with_disclosure";
    });
    expect(usable.length).toBeGreaterThan(0);
    const approvedId = usable[0].id;
    routeFetch((url) => {
      if (url.endsWith("/api/cases/case-3407")) {
        return jsonResponse({
          ...payload,
          reportSections: [
            ...payload.reportSections.map((section) => ({
              ...section,
              approvalState: section.id === approvedId ? "approved" : "draft",
            })),
            {
              ...usable[0],
              id: "rpt-usable-unapproved",
              paragraphRef: "TEST-DRAFT-1",
              approvalState: "draft",
            },
          ],
        });
      }
      return null;
    });
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const exportNav = within(
      screen.getByRole("navigation", { name: "Case sections" }),
    ).getByRole("button", { name: /Evidence packet/ });
    expect(exportNav.querySelector(".nav-count")).toHaveTextContent("1");
    expect(exportNav.querySelector(".nav-count")).toHaveAttribute(
      "title",
      "Approved report sections currently includable in a production packet",
    );
  });

  it("opens the Claims ledger when a report citation has no related conflict", async () => {
    const payload = casePayload();
    const unlinkedClaim = { ...payload.claims[0], relatedConflictIds: [] };
    routeFetch((url) => {
      if (url.endsWith("/api/cases/case-3407")) {
        return jsonResponse({
          ...payload,
          claims: payload.claims.map((claim) =>
            claim.id === unlinkedClaim.id ? unlinkedClaim : claim,
          ),
          reportSections: [
            { ...payload.reportSections[0], claimIds: [unlinkedClaim.id] },
          ],
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const nav = screen.getByRole("navigation", { name: "Case sections" });
    await user.click(within(nav).getByRole("button", { name: /Reports/ }));
    const workspace = screen.getByRole("main", { name: "Workspace" });
    await user.click(within(workspace).getByTitle(unlinkedClaim.text));

    expect(
      await within(workspace).findByRole("heading", { name: "Claims ledger" }),
    ).toBeInTheDocument();
  });

  it("shows the matter picker after opening a second matter", async () => {
    // Regression: creating a matter switched into it but left `availableCases`
    // holding only the original, so the picker (which renders only for more
    // than one matter) stayed hidden and the reviewer had no way back to their
    // other matters without reloading the page.
    const newCase = {
      ...sample.caseFile,
      id: "case-new",
      name: "Trans-Pacific 118 Runway Excursion",
      docketRef: "DCA26MA004",
    };
    const newSummary = {
      caseFile: newCase,
      caseMembership: {
        caseId: newCase.id,
        reviewerId: "rev-okafor",
        role: "Senior Aviation Counsel" as const,
        isActive: true,
      },
    };
    let matterCreated = false;
    routeFetch((url, init) => {
      if (url.endsWith("/api/cases") && init?.method === "POST") {
        matterCreated = true;
        return jsonResponse({
          caseFile: newCase,
          caseMembership: newSummary.caseMembership,
        }, 201);
      }
      // The list must reflect the new matter once it exists.
      if (url.endsWith("/api/cases") && init?.method === undefined) {
        return jsonResponse({
          cases: matterCreated ? [...caseSummaries(), newSummary] : caseSummaries(),
        });
      }
      if (url.endsWith("/api/cases/case-new")) {
        return jsonResponse({
          ...casePayload(),
          caseFile: newCase,
          caseMembership: newSummary.caseMembership,
        });
      }
      return null;
    });
    renderControlledApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    // Only one matter so far, so there is nothing to switch between.
    expect(screen.queryByLabelText("Matter")).toBeNull();

    await act(async () => {
      await currentStore().createMatter({
        name: "Trans-Pacific 118 Runway Excursion",
        aircraft: "Boeing 777-300ER (N881TP)",
        accidentDate: "2025-11-02",
        location: "Anchorage, Alaska",
        matterType: "wrongful_death",
        docketRef: "DCA26MA004",
      });
    });

    const picker = await screen.findByLabelText("Matter");
    expect(picker).toHaveValue("case-new");
    expect(
      [...(picker as HTMLSelectElement).options].map((option) => option.value),
    ).toContain(sample.caseFile.id);
  });

  it("does not let stale matter creation restore loading after logout", async () => {
    const creationResponse = deferred<Response>();
    routeFetch((url, init) => {
      if (url.endsWith("/api/cases") && init?.method === "POST") {
        return creationResponse.promise;
      }
      if (url.endsWith("/api/auth/logout") && init?.method === "POST") {
        return jsonResponse({ status: "signed_out" });
      }
      return null;
    });
    renderControlledApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const creation = currentStore().createMatter({
      name: "Late Matter",
      aircraft: "Boeing 737",
      accidentDate: "2025-11-02",
      location: "Anchorage, Alaska",
      matterType: "wrongful_death",
      docketRef: "DCA26MA099",
    });
    await act(async () => {
      await currentStore().logout();
    });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();

    let result!: Awaited<typeof creation>;
    await act(async () => {
      creationResponse.resolve(
        jsonResponse({
          caseFile: { ...sample.caseFile, id: "case-late", name: "Late Matter" },
          caseMembership: {
            caseId: "case-late",
            reviewerId: "rev-okafor",
            role: "Senior Aviation Counsel",
            isActive: true,
          },
        }, 201),
      );
      result = await creation;
    });

    expect(result).toEqual({
      ok: false,
      error: "This operation was superseded by a session or matter change.",
    });
    expect(currentStore().state.bootStatus).toBe("unauthenticated");
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("switches between assigned matters", async () => {
    const secondCase = {
      ...sample.caseFile,
      id: "case-901",
      name: "Airline Maintenance Dispute",
      docketRef: "NTSB DCA26MA901",
    };
    routeFetch((url) => {
      if (url.endsWith("/api/cases")) {
        return jsonResponse({
          cases: [
            ...caseSummaries(),
            {
              caseFile: secondCase,
              caseMembership: {
                caseId: secondCase.id,
                reviewerId: "rev-okafor",
                role: "Senior Aviation Counsel",
                isActive: true,
              },
            },
          ],
        });
      }
      if (url.endsWith("/api/cases/case-901")) {
        return jsonResponse({
          ...casePayload(),
          caseFile: secondCase,
          caseMembership: {
            caseId: secondCase.id,
            reviewerId: "rev-okafor",
            role: "Senior Aviation Counsel",
            isActive: true,
          },
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();

    await screen.findAllByText("Colgan Air Flight 3407");
    await user.selectOptions(screen.getByLabelText("Matter"), "case-901");

    expect(await screen.findAllByText("NTSB DCA26MA901")).not.toHaveLength(0);
    expect(screen.getByLabelText("Matter")).toHaveValue("case-901");
  });

  it("keeps the newest matter when rapid switch responses arrive out of order", async () => {
    const caseA = casePayloadFor("case-a", "Matter A");
    const caseB = casePayloadFor("case-b", "Matter B");
    const caseAResponse = deferred<Response>();
    const caseBResponse = deferred<Response>();
    routeFetch((url, init) => {
      if (init?.method === undefined && url.endsWith("/api/cases/case-a")) {
        return caseAResponse.promise;
      }
      if (init?.method === undefined && url.endsWith("/api/cases/case-b")) {
        return caseBResponse.promise;
      }
      return null;
    });
    renderControlledApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const store = currentStore();
    let switchA!: ReturnType<typeof store.switchCase>;
    let switchB!: ReturnType<typeof store.switchCase>;
    act(() => {
      switchA = store.switchCase("case-a");
      switchB = store.switchCase("case-b");
    });

    let switchBResult!: Awaited<typeof switchB>;
    await act(async () => {
      caseBResponse.resolve(jsonResponse(caseB));
      switchBResult = await switchB;
    });
    expect(switchBResult.ok).toBe(true);
    expect(currentStore().state.caseFile.id).toBe("case-b");

    let switchAResult!: Awaited<typeof switchA>;
    await act(async () => {
      caseAResponse.resolve(jsonResponse(caseA));
      switchAResult = await switchA;
    });
    expect(switchAResult).toEqual({
      ok: false,
      error: "This operation was superseded by a session or matter change.",
    });
    expect(currentStore().state.caseFile.id).toBe("case-b");
    expect(currentStore().state.caseFile.name).toBe("Matter B");
  });

  it("does not merge an in-flight matter-A mutation into matter B", async () => {
    const mutationResponse = deferred<Response>();
    const caseBResponse = deferred<Response>();
    const caseB = casePayloadFor("case-b", "Matter B");
    routeFetch((url, init) => {
      if (init?.method === "POST" && url.endsWith("/conflicts/cf-1/decisions")) {
        return mutationResponse.promise;
      }
      if (init?.method === undefined && url.endsWith("/api/cases/case-b")) {
        return caseBResponse.promise;
      }
      return null;
    });
    renderControlledApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const mutation = currentStore().saveDecision({
      conflictId: "cf-1",
      decisionType: "preserve_both",
      selectedClaimId: null,
      reasoning: "Preserve both claims while the record is reconciled.",
    });
    let switchToB!: ReturnType<ReturnType<typeof useStore>["switchCase"]>;
    act(() => {
      switchToB = currentStore().switchCase("case-b");
    });
    await act(async () => {
      caseBResponse.resolve(jsonResponse(caseB));
      await switchToB;
    });

    let mutationResult!: Awaited<typeof mutation>;
    await act(async () => {
      mutationResponse.resolve(jsonResponse(decisionResponse(), 201));
      mutationResult = await mutation;
    });

    expect(mutationResult.ok).toBe(false);
    expect(currentStore().state.caseFile.id).toBe("case-b");
    expect(currentStore().state.lastSavedDecisionId).toBeNull();
    expect(currentStore().state.decisions.has("srv-1")).toBe(false);
  });

  it("does not apply a stale-write refresh for matter A after switching to B", async () => {
    const mutationResponse = deferred<Response>();
    const refreshResponse = deferred<Response>();
    const refreshStarted = deferred<void>();
    const caseBResponse = deferred<Response>();
    const caseB = casePayloadFor("case-b", "Matter B");
    let interceptRefresh = false;
    routeFetch((url, init) => {
      if (init?.method === "POST" && url.endsWith("/conflicts/cf-1/decisions")) {
        return mutationResponse.promise;
      }
      if (
        interceptRefresh &&
        init?.method === undefined &&
        url.endsWith("/api/cases/case-3407")
      ) {
        refreshStarted.resolve();
        return refreshResponse.promise;
      }
      if (init?.method === undefined && url.endsWith("/api/cases/case-b")) {
        return caseBResponse.promise;
      }
      return null;
    });
    renderControlledApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const mutation = currentStore().saveDecision({
      conflictId: "cf-1",
      decisionType: "preserve_both",
      selectedClaimId: null,
      reasoning: "Preserve both claims while the record is reconciled.",
    });
    interceptRefresh = true;
    await act(async () => {
      mutationResponse.resolve(jsonResponse({ detail: "Conflict version changed." }, 409));
      await refreshStarted.promise;
    });

    let switchToB!: ReturnType<ReturnType<typeof useStore>["switchCase"]>;
    act(() => {
      switchToB = currentStore().switchCase("case-b");
    });
    await act(async () => {
      caseBResponse.resolve(jsonResponse(caseB));
      await switchToB;
    });
    let mutationResult!: Awaited<typeof mutation>;
    await act(async () => {
      refreshResponse.resolve(jsonResponse(casePayload()));
      mutationResult = await mutation;
    });

    expect(mutationResult.ok).toBe(false);
    expect(currentStore().state.caseFile.id).toBe("case-b");
    expect(currentStore().state.caseFile.name).toBe("Matter B");
  });

  it("ignores an old-session mutation response after logout and a new login", async () => {
    const oldMutationResponse = deferred<Response>();
    const newCase = casePayloadFor("case-new", "New Session Matter");
    let newLogin = false;
    routeFetch((url, init) => {
      if (init?.method === "POST" && url.endsWith("/conflicts/cf-1/decisions")) {
        return oldMutationResponse.promise;
      }
      if (url.endsWith("/api/auth/logout") && init?.method === "POST") {
        return jsonResponse({ status: "signed_out" });
      }
      if (url.endsWith("/api/auth/login") && init?.method === "POST") {
        newLogin = true;
        return jsonResponse(sessionInfo());
      }
      if (newLogin && url.endsWith("/api/cases") && init?.method === undefined) {
        return jsonResponse({
          cases: [
            {
              caseFile: newCase.caseFile,
              caseMembership: newCase.caseMembership,
            },
          ],
        });
      }
      if (
        newLogin &&
        url.endsWith("/api/cases/case-new") &&
        init?.method === undefined
      ) {
        return jsonResponse(newCase);
      }
      return null;
    });
    renderControlledApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const oldMutation = currentStore().saveDecision({
      conflictId: "cf-1",
      decisionType: "preserve_both",
      selectedClaimId: null,
      reasoning: "Preserve both claims while the record is reconciled.",
    });
    await act(async () => {
      await currentStore().logout();
    });
    let loginResult!: Awaited<ReturnType<ReturnType<typeof useStore>["login"]>>;
    await act(async () => {
      loginResult = await currentStore().login("mokafor", "new-session-password");
    });
    expect(loginResult.ok).toBe(true);
    expect(currentStore().state.caseFile.id).toBe("case-new");

    let oldMutationResult!: Awaited<typeof oldMutation>;
    await act(async () => {
      oldMutationResponse.resolve(jsonResponse({ detail: "Not signed in." }, 401));
      oldMutationResult = await oldMutation;
    });

    expect(oldMutationResult.ok).toBe(false);
    expect(currentStore().state.sessionReviewer?.id).toBe(OKAFOR.id);
    expect(currentStore().state.caseFile.id).toBe("case-new");
    expect(currentStore().state.bootStatus).toBe("ready");
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("shows the sign-in screen when there is no session, then logs in", async () => {
    let authenticated = false;
    routeFetch((url, init) => {
      if (url.endsWith("/api/auth/session")) {
        return authenticated
          ? jsonResponse(sessionInfo())
          : jsonResponse({ detail: "Not signed in." }, 401);
      }
      if (url.endsWith("/api/auth/login") && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        if (body.password !== "argus-demo") {
          return jsonResponse({ detail: "Invalid username or password." }, 401);
        }
        authenticated = true;
        return jsonResponse(sessionInfo());
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();

    await screen.findByRole("heading", { name: "Sign in" });

    // Wrong password: uniform failure message.
    await user.type(screen.getByLabelText("Username"), "mokafor");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByText("Invalid username or password."),
    ).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Password"));
    await user.type(screen.getByLabelText("Password"), "argus-demo");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findAllByText("Colgan Air Flight 3407")).not.toHaveLength(0);
  });

  it("forces password rotation before entering the case", async () => {
    let rotated = false;
    let caseListRequests = 0;
    routeFetch((url, init) => {
      if (url.endsWith("/api/auth/session")) {
        return jsonResponse(sessionInfo({ mustChangePassword: !rotated }));
      }
      if (url.endsWith("/api/cases") && init?.method === undefined) {
        caseListRequests += 1;
        if (!rotated) {
          return jsonResponse({ detail: "Password change required." }, 403);
        }
        return null;
      }
      if (url.endsWith("/api/auth/change-password") && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        if (body.newPassword.length < 10) {
          return jsonResponse({ detail: "New password must be at least 10 characters." }, 422);
        }
        rotated = true;
        return jsonResponse({ status: "changed" });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();

    // Gate first — the case is not shown.
    await screen.findByRole("heading", { name: "Change password" });
    expect(screen.getByText(/shared initial password/)).toBeInTheDocument();
    expect(screen.queryByText("Colgan Air Flight 3407")).not.toBeInTheDocument();
    // Forced gate offers sign-out, not cancel.
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    expect(caseListRequests).toBe(0);

    await user.type(screen.getByLabelText("Current password"), "argus-demo");
    await user.type(screen.getByLabelText("New password"), "a-much-better-password");
    await user.type(
      screen.getByLabelText("Confirm new password"),
      "a-much-better-password",
    );
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findAllByText("Colgan Air Flight 3407")).not.toHaveLength(0);
    expect(caseListRequests).toBe(1);
  });

  it("supports voluntary password change from the header", async () => {
    routeFetch((url, init) => {
      if (url.endsWith("/api/auth/change-password") && init?.method === "POST") {
        return jsonResponse({ status: "changed" });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    await user.click(screen.getByRole("button", { name: "Change password" }));
    await screen.findByRole("heading", { name: "Change password" });
    // Voluntary gate can be cancelled back to the app.
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findAllByText("Colgan Air Flight 3407")).not.toHaveLength(0);
  });

  it("signs out back to the sign-in screen", async () => {
    routeFetch((url, init) => {
      if (url.endsWith("/api/auth/logout") && init?.method === "POST") {
        return jsonResponse({ status: "signed_out" });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("keeps the session after a mistyped MFA code and permits a retry", async () => {
    let attempts = 0;
    routeFetch((url, init) => {
      if (url.endsWith("/api/auth/session")) {
        return jsonResponse(sessionInfo({ mfa: { enabled: true, verified: false } }));
      }
      if (url.endsWith("/api/auth/mfa/verify") && init?.method === "POST") {
        attempts += 1;
        const { code } = JSON.parse(init.body as string) as { code: string };
        return code === "123456"
          ? jsonResponse({ enabled: true, verified: true })
          : jsonResponse({ detail: "Invalid MFA code." }, 422);
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    await user.click(screen.getByRole("button", { name: "Verify MFA" }));
    await user.type(screen.getByLabelText("MFA code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("Invalid MFA code.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Verify MFA" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText("MFA code"));
    await user.type(screen.getByLabelText("MFA code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findAllByText("Colgan Air Flight 3407")).not.toHaveLength(0);
    expect(attempts).toBe(2);
  });

  it("steps up from a decision without losing the draft", async () => {
    routeFetch((url, init) => {
      if (url.endsWith("/api/auth/session")) {
        return jsonResponse(sessionInfo({ mfa: { enabled: true, verified: false } }));
      }
      if (url.endsWith("/api/auth/mfa/verify") && init?.method === "POST") {
        return jsonResponse({ enabled: true, verified: true });
      }
      if (url.endsWith("/conflicts/cf-1/decisions") && init?.method === "POST") {
        return jsonResponse(decisionResponse(), 201);
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    await user.click(screen.getByRole("radio", { name: /Preserve both claims/ }));
    const reasoning = screen.getByLabelText(/Reasoning note/);
    await user.type(
      reasoning,
      "Preserve both accounts while counsel reconciles the timing evidence.",
    );
    await user.click(screen.getByRole("button", { name: "Save decision" }));

    expect(screen.getByRole("heading", { name: "Verify MFA" })).toBeInTheDocument();
    expect(reasoning).toHaveValue(
      "Preserve both accounts while counsel reconciles the timing evidence.",
    );

    await user.type(screen.getByLabelText("MFA code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Verify MFA" })).not.toBeInTheDocument(),
    );
    const save = screen.getByRole("button", { name: "Save decision" });
    expect(save).toHaveFocus();
    expect(reasoning).toHaveValue(
      "Preserve both accounts while counsel reconciles the timing evidence.",
    );

    await user.click(save);
    expect(await screen.findByRole("status")).toHaveTextContent(/Decision recorded:/);
  });

  it("revalidates on focus and clears loaded matter data when the session is revoked", async () => {
    let revoked = false;
    let sessionReads = 0;
    routeFetch((url) => {
      if (url.endsWith("/api/auth/session")) {
        sessionReads += 1;
        return revoked
          ? jsonResponse({ detail: "Not signed in." }, 401)
          : jsonResponse(sessionInfo());
      }
      return null;
    });
    renderAppWithProbe();
    await screen.findAllByText("Colgan Air Flight 3407");
    expect(screen.getByLabelText("Sensitive state probe")).toHaveTextContent(
      /^case-3407\|[1-9]\d*\|[1-9]\d*$/,
    );

    revoked = true;
    act(() => window.dispatchEvent(new Event("focus")));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText("Sensitive state probe")).toHaveTextContent("|0|0"),
    );
    expect(sessionReads).toBeGreaterThanOrEqual(2);
  });

  it("rejects an elapsed server session before loading matter data", async () => {
    let caseListRequests = 0;
    routeFetch((url, init) => {
      if (url.endsWith("/api/auth/session")) {
        return jsonResponse(sessionInfo({ expiresAt: "2020-01-01T00:00:00Z" }));
      }
      if (url.endsWith("/api/cases") && init?.method === undefined) {
        caseListRequests += 1;
      }
      return null;
    });

    renderAppWithProbe();

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByLabelText("Sensitive state probe")).toHaveTextContent("|0|0");
    expect(caseListRequests).toBe(0);
  });

  it("routes decisions through the API and applies the server's entities", async () => {
    const response = decisionResponse();
    routeFetch((url, init) => {
      if (init?.method === "POST" && url.endsWith("/conflicts/cf-1/decisions")) {
        return jsonResponse(response, 201);
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    await user.click(screen.getByRole("radio", { name: /Preserve both claims/ }));
    await user.type(
      screen.getByLabelText(/Reasoning note/),
      "Both accounts preserved pending expert reconciliation of the timeline.",
    );
    await user.click(screen.getByRole("button", { name: "Save decision" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      /Decision recorded: Preserve both claims/,
    );
    const history = screen.getByRole("region", { name: "Decision history" });
    expect(within(history).getByText("Preserve both claims")).toBeInTheDocument();

    const call = fetchMock.mock.calls.find(
      (args) =>
        (args[1] as RequestInit | undefined)?.method === "POST" &&
        String(args[0]).endsWith("/api/cases/case-3407/conflicts/cf-1/decisions"),
    );
    expect(call).toBeDefined();
    const body = JSON.parse((call![1] as RequestInit).body as string);
    expect(body).toMatchObject({
      decisionType: "preserve_both",
      reasoning: expect.stringContaining("expert reconciliation"),
      expectedVersion: 1,
    });
    // Identity comes from the session cookie, never the body.
    expect(body).not.toHaveProperty("reviewerId");
  });

  it("refreshes a stale decision conflict and preserves the unsaved reasoning", async () => {
    let caseReads = 0;
    routeFetch((url, init) => {
      if (url.endsWith("/api/cases/case-3407") && init?.method === undefined) {
        caseReads += 1;
        const payload = casePayload();
        return jsonResponse(
          caseReads === 1
            ? payload
            : {
                ...payload,
                conflicts: payload.conflicts.map((conflict) =>
                  conflict.id === "cf-1" ? { ...conflict, version: 2 } : conflict,
                ),
              },
        );
      }
      if (init?.method === "POST" && url.endsWith("/conflicts/cf-1/decisions")) {
        const body = JSON.parse(init.body as string);
        expect(body.expectedVersion).toBe(1);
        return jsonResponse(
          { detail: "Conflict changed since this review began." },
          409,
        );
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const reasoning = "Preserve both accounts while the updated record is reviewed.";
    await user.click(screen.getByRole("radio", { name: /Preserve both claims/ }));
    await user.type(screen.getByLabelText(/Reasoning note/), reasoning);
    await user.click(screen.getByRole("button", { name: "Save decision" }));

    expect(
      await screen.findByText(/The latest matter state has been loaded/),
    ).toBeInTheDocument();
    expect(screen.getByText(/unsaved input remains in this form/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Reasoning note/)).toHaveValue(reasoning);
    expect(caseReads).toBe(2);
  });

  it("surfaces the server's domain error message in the form", async () => {
    routeFetch((url, init) => {
      if (init?.method === "POST" && url.includes("/decisions")) {
        return jsonResponse(
          { detail: "Critical-severity conflicts require Senior Aviation Counsel." },
          403,
        );
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    await user.click(screen.getByRole("radio", { name: /Preserve both claims/ }));
    await user.type(
      screen.getByLabelText(/Reasoning note/),
      "Long enough reasoning for the client-side gate to pass.",
    );
    await user.click(screen.getByRole("button", { name: "Save decision" }));

    expect(
      await screen.findByText(/Critical-severity conflicts require Senior Aviation Counsel/),
    ).toBeInTheDocument();
  });

  it("flips to the sign-in screen when the session expires mid-work", async () => {
    routeFetch((url, init) => {
      if (init?.method === "POST" && url.includes("/decisions")) {
        return jsonResponse({ detail: "Not signed in." }, 401);
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    await user.click(screen.getByRole("radio", { name: /Preserve both claims/ }));
    await user.type(
      screen.getByLabelText(/Reasoning note/),
      "Long enough reasoning for the client-side gate to pass.",
    );
    await user.click(screen.getByRole("button", { name: "Save decision" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("generates evidence packets server-side and shows the server's manifest", async () => {
    routeFetch((url, init) => {
      if (
        init?.method === "POST" &&
        url.endsWith("/api/cases/case-3407/packets")
      ) {
        const body = JSON.parse(init.body as string);
        expect(body.packetType).toBe("internal");
        expect(body.idempotencyKey).toEqual(expect.any(String));
        return jsonResponse(
          {
            packetId: "pkt-srv-1",
            packetType: "internal",
            generatedAt: "2026-07-11T09:00:00Z",
            generatedByName: "Miriam Okafor",
            generatedByRole: "Senior Aviation Counsel",
            stats: { included: 6, excluded: 0, withheld: 0 },
            entries: [
              {
                sectionId: "rpt-pc",
                revisionId: "rsr-pc-1",
                title: "Probable Cause",
                paragraphRef: "PC-1",
                impactStatus: "blocked",
                disposition: "included",
                reason: "Included for internal review — BLOCKED for report use.",
              },
            ],
            sha256: "ab".repeat(32),
            filename: "atlas-argus-internal-packet-case-3407-20260711T0900.html",
            document: "<!doctype html><html><body>Evidence Packet (server)</body></html>",
            auditEvent: {
              id: "aud-srv-1",
              caseId: "case-3407",
              at: "2026-07-11T09:00:00Z",
              actor: "Miriam Okafor",
              actorRole: "Senior Aviation Counsel",
              action: "evidence packet generated",
              subjectType: "export",
              subjectId: "pkt-srv-1",
              previousStatus: null,
              newStatus: null,
              detail: "internal packet: 6 section(s) included…",
              reportEligibilityChanged: false,
            },
          },
          201,
        );
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const nav = screen.getByRole("navigation", { name: "Case sections" });
    await user.click(within(nav).getByRole("button", { name: /Evidence packet/ }));
    await user.click(screen.getByRole("button", { name: "Generate packet" }));

    const generatedStatus = await screen.findByRole("status");
    expect(generatedStatus).toHaveTextContent(
      /Packet generated: internal, 6 section\(s\) included/,
    );
    expect(generatedStatus).toHaveTextContent(
      /Miriam Okafor · Senior Aviation Counsel/,
    );
    const manifest = screen.getByRole("region", { name: "Packet manifest" });
    expect(within(manifest).getByText("Probable Cause")).toBeInTheDocument();
    expect(screen.getByTitle("Evidence packet preview")).toHaveAttribute(
      "srcdoc",
      expect.stringContaining("Evidence Packet (server)"),
    );
  });

  it("opens a stored packet from the export view", async () => {
    routeFetch((url, init) => {
      if (
        init?.method === undefined &&
        url.includes("/api/cases/case-3407/packets?limit=25")
      ) {
        return jsonResponse({
          caseId: "case-3407",
          total: 1,
          limit: 25,
          offset: 0,
          verification: { ok: true, rootIntegrityHash: "cd".repeat(32), checked: 1 },
          packets: [
            {
              packetId: "pkt-stored-1",
              caseId: "case-3407",
              packetType: "production",
              generatedAt: "2026-07-11T09:00:00Z",
              generatedByReviewerId: "rev-okafor",
              generatedByName: "Miriam Okafor",
              generatedByRole: "Senior Aviation Counsel",
              filename: "stored.html",
              bodySha256: "ab".repeat(32),
              documentSha256: "bc".repeat(32),
              manifestSha256: "cd".repeat(32),
              artifactSha256: "de".repeat(32),
              packetIntegrityHash: "ef".repeat(32),
              stats: { included: 1, excluded: 0, withheld: 0 },
              integrityOk: true,
            },
          ],
        });
      }
      if (
        init?.method === undefined &&
        url.endsWith("/api/cases/case-3407/packets/pkt-stored-1")
      ) {
        return jsonResponse({
          packetId: "pkt-stored-1",
          caseId: "case-3407",
          packetType: "production",
          generatedAt: "2026-07-11T09:00:00Z",
          generatedByReviewerId: "rev-okafor",
          generatedByName: "Miriam Okafor",
          generatedByRole: "Senior Aviation Counsel",
          filename: "stored.html",
          bodySha256: "ab".repeat(32),
          documentSha256: "bc".repeat(32),
          manifestSha256: "cd".repeat(32),
          artifactSha256: "de".repeat(32),
          packetIntegrityHash: "ef".repeat(32),
          stats: { included: 1, excluded: 0, withheld: 0 },
          integrityOk: true,
          manifest: {
            entries: [
              {
                sectionId: "rpt-acft",
                title: "Aircraft",
                paragraphRef: "ACFT-1",
                impactStatus: "eligible",
                disposition: "included",
                reason: "Included.",
              },
            ],
          },
          document: "<!doctype html><html><body>Stored packet body</body></html>",
          verification: {
            ok: true,
            packetIssues: [],
            chain: { ok: true, issues: [] },
          },
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const nav = screen.getByRole("navigation", { name: "Case sections" });
    await user.click(within(nav).getByRole("button", { name: /Evidence packet/ }));
    const pastPackets = screen.getByRole("region", { name: "Past packets" });
    expect(await within(pastPackets).findByText(/1 stored packet/)).toBeInTheDocument();

    await user.click(
      within(pastPackets).getByRole("button", {
        name: /Open production packet generated.*pkt-stored-1/,
      }),
    );
    expect(await screen.findByRole("region", { name: "Packet manifest" })).toBeInTheDocument();
    expect(screen.getByTitle("Evidence packet preview")).toHaveAttribute(
      "srcdoc",
      expect.stringContaining("Stored packet body"),
    );
  });

  it("quarantines a stored packet when detail verification fails", async () => {
    routeFetch((url, init) => {
      if (
        init?.method === undefined &&
        url.includes("/api/cases/case-3407/packets?limit=25")
      ) {
        return jsonResponse({
          caseId: "case-3407",
          total: 1,
          limit: 25,
          offset: 0,
          verification: { ok: true, rootIntegrityHash: "cd".repeat(32), checked: 1 },
          packets: [
            {
              packetId: "pkt-tampered-1",
              caseId: "case-3407",
              packetType: "production",
              generatedAt: "2026-07-11T09:00:00Z",
              generatedByReviewerId: "rev-okafor",
              generatedByName: "Miriam Okafor",
              generatedByRole: "Senior Aviation Counsel",
              filename: "tampered.html",
              bodySha256: "ab".repeat(32),
              documentSha256: "bc".repeat(32),
              manifestSha256: "cd".repeat(32),
              artifactSha256: "de".repeat(32),
              packetIntegrityHash: "ef".repeat(32),
              stats: { included: 1, excluded: 0, withheld: 0 },
              // The list was verified first; the detail check catches a later change.
              integrityOk: true,
            },
          ],
        });
      }
      if (
        init?.method === undefined &&
        url.endsWith("/api/cases/case-3407/packets/pkt-tampered-1")
      ) {
        return jsonResponse({
          packetId: "pkt-tampered-1",
          caseId: "case-3407",
          packetType: "production",
          generatedAt: "2026-07-11T09:00:00Z",
          generatedByReviewerId: "rev-okafor",
          generatedByName: "Miriam Okafor",
          generatedByRole: "Senior Aviation Counsel",
          filename: "tampered.html",
          bodySha256: "ab".repeat(32),
          documentSha256: "bc".repeat(32),
          manifestSha256: "cd".repeat(32),
          artifactSha256: "de".repeat(32),
          packetIntegrityHash: "ef".repeat(32),
          stats: { included: 1, excluded: 0, withheld: 0 },
          integrityOk: false,
          manifest: { entries: [] },
          document: "<!doctype html><html><body>TAMPERED CONTENT</body></html>",
          verification: {
            ok: false,
            packetIssues: [{ id: "pkt-tampered-1", kind: "document_sha256_mismatch" }],
            chain: { ok: true, issues: [] },
          },
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const nav = screen.getByRole("navigation", { name: "Case sections" });
    await user.click(within(nav).getByRole("button", { name: /Evidence packet/ }));
    const pastPackets = screen.getByRole("region", { name: "Past packets" });
    await within(pastPackets).findByText(/1 stored packet/);
    await user.click(
      within(pastPackets).getByRole("button", {
        name: /Open production packet generated.*pkt-tampered-1/,
      }),
    );

    const quarantine = await screen.findByRole("alert", { name: "Quarantined packet" });
    expect(quarantine).toHaveTextContent(/failed integrity verification and was not opened/i);
    expect(quarantine).toHaveTextContent("document_sha256_mismatch");
    expect(screen.queryByRole("button", { name: /Download packet/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Print/ })).not.toBeInTheDocument();
    expect(screen.queryByTitle("Evidence packet preview")).not.toBeInTheDocument();
  });

  it("lets senior counsel provision reviewers from the admin surface", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    const adminReviewers = sample.reviewers.map((reviewer, index) => ({
      ...reviewer,
      username: ["mokafor", "dreeves", "pnatarajan", "tlindqvist"][index],
      isActive: true,
      mustChangePassword: false,
      passwordChangedAt: null,
      mfaEnabled: reviewer.id === "rev-natarajan",
    }));
    routeFetch((url, init) => {
      if (url.endsWith("/api/admin/reviewers") && init?.method === undefined) {
        return jsonResponse({ reviewers: adminReviewers });
      }
      if (url.endsWith("/api/admin/account-audit") && init?.method === undefined) {
        return jsonResponse({ events: [] });
      }
      if (
        url.endsWith("/api/admin/cases/case-3407/members") &&
        init?.method === undefined
      ) {
        return jsonResponse({
          members: [
            {
              caseId: "case-3407",
              reviewerId: "rev-okafor",
              reviewerName: "Miriam Okafor",
              username: "mokafor",
              reviewerRole: "Senior Aviation Counsel",
              role: "Senior Aviation Counsel",
              isActive: true,
              reviewerIsActive: true,
            },
          ],
        });
      }
      if (url.endsWith("/api/admin/reviewers") && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toEqual({
          name: "Avery Chen",
          username: "achen",
          role: "Claims Reviewer",
          initialPassword: "temporary-password",
        });
        const reviewer = {
          id: "rev-avery",
          name: "Avery Chen",
          username: "achen",
          role: "Claims Reviewer",
          isActive: true,
          mustChangePassword: true,
          passwordChangedAt: null,
          mfaEnabled: false,
        };
        return jsonResponse(
          {
            reviewer,
            accountAuditEvent: {
              id: "aud-admin-1",
              at: "2026-07-11T10:00:00Z",
              actorReviewerId: "rev-okafor",
              actor: "Miriam Okafor",
              actorRole: "Senior Aviation Counsel",
              action: "reviewer provisioned",
              subjectReviewerId: "rev-avery",
              previousStatus: null,
              newStatus: "active",
              detail: "Avery Chen provisioned.",
            },
          },
          201,
        );
      }
      if (
        url.endsWith("/api/admin/reviewers/rev-natarajan/reset-mfa") &&
        init?.method === "POST"
      ) {
        const reviewer = adminReviewers.find(
          (candidate) => candidate.id === "rev-natarajan",
        )!;
        return jsonResponse({
          reviewer: { ...reviewer, mfaEnabled: false },
          accountAuditEvent: {
            id: "aud-admin-mfa-1",
            at: "2026-07-11T10:02:00Z",
            actorReviewerId: "rev-okafor",
            actor: "Miriam Okafor",
            actorRole: "Senior Aviation Counsel",
            action: "reviewer MFA reset",
            subjectReviewerId: "rev-natarajan",
            previousStatus: "enabled",
            newStatus: "not_enrolled",
            detail: "Priya Natarajan must enroll a new MFA factor.",
          },
        });
      }
      if (url.endsWith("/api/admin/cases/case-3407/members") && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toEqual({
          reviewerId: "rev-natarajan",
          role: "Claims Reviewer",
        });
        return jsonResponse(
          {
            member: {
              caseId: "case-3407",
              reviewerId: "rev-natarajan",
              reviewerName: "Priya Natarajan",
              username: "pnatarajan",
              reviewerRole: "Claims Reviewer",
              role: "Claims Reviewer",
              isActive: true,
              reviewerIsActive: true,
            },
            accountAuditEvent: {
              id: "aud-admin-2",
              at: "2026-07-11T10:05:00Z",
              actorReviewerId: "rev-okafor",
              actor: "Miriam Okafor",
              actorRole: "Senior Aviation Counsel",
              action: "case member added",
              subjectReviewerId: "rev-natarajan",
              previousStatus: null,
              newStatus: "active",
              detail: "Priya Natarajan granted access.",
            },
          },
          201,
        );
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const nav = screen.getByRole("navigation", { name: "Case sections" });
    await user.click(within(nav).getByRole("button", { name: /Reviewer admin/ }));

    await screen.findByRole("heading", { name: "Reviewer administration" });
    const accounts = screen.getByRole("region", { name: "Reviewer accounts" });
    expect(await within(accounts).findByText("Miriam Okafor")).toBeInTheDocument();

    await user.click(
      within(accounts).getByRole("button", { name: "Reset MFA for Priya Natarajan" }),
    );
    expect(
      await screen.findByText(
        "Priya Natarajan's MFA factor was reset; sessions were revoked and re-enrollment is required.",
      ),
    ).toBeInTheDocument();
    expect(within(accounts).getAllByText("enrollment required").length).toBeGreaterThan(0);

    await user.type(screen.getByLabelText("Name"), "Avery Chen");
    await user.type(screen.getByLabelText("Username"), "achen");
    await user.type(screen.getByLabelText("Initial password"), "temporary-password");
    await user.click(screen.getByRole("button", { name: "Create reviewer" }));

    expect(
      await screen.findByText("Avery Chen provisioned; password rotation required."),
    ).toBeInTheDocument();
    const accountAudit = screen.getByRole("region", { name: "Account audit" });
    expect(within(accountAudit).getByText("reviewer provisioned")).toBeInTheDocument();

    const matterAccess = screen.getByRole("region", { name: "Matter access" });
    expect(await within(matterAccess).findByText("Miriam Okafor")).toBeInTheDocument();
    expect(within(matterAccess).getByText("mokafor")).toBeInTheDocument();
    await user.selectOptions(
      within(matterAccess).getByLabelText("Reviewer"),
      "rev-natarajan",
    );
    await user.click(within(matterAccess).getByRole("button", { name: "Grant access" }));

    expect(
      await screen.findByText("Priya Natarajan granted access to this matter."),
    ).toBeInTheDocument();
    expect(within(matterAccess).getByText("Priya Natarajan")).toBeInTheDocument();
  });

  it("sends section versions and retries a stale revision against refreshed state", async () => {
    const section = sample.reportSections[0];
    const updatedText =
      "The revised report paragraph retains its supporting source citations and updated analysis.";
    const submittedVersions: number[] = [];
    let caseReads = 0;
    routeFetch((url, init) => {
      if (url.endsWith("/api/cases/case-3407") && init?.method === undefined) {
        caseReads += 1;
        const payload = casePayload();
        return jsonResponse(
          caseReads === 1
            ? payload
            : {
                ...payload,
                reportSections: payload.reportSections.map((candidate) =>
                  candidate.id === section.id ? { ...candidate, version: 2 } : candidate,
                ),
              },
        );
      }
      if (
        url.endsWith(`/api/cases/case-3407/report-sections/${section.id}`) &&
        init?.method === "PUT"
      ) {
        const body = JSON.parse(init.body as string) as { expectedVersion: number };
        submittedVersions.push(body.expectedVersion);
        if (submittedVersions.length === 1) {
          return jsonResponse({ detail: "Report section has a newer revision." }, 409);
        }
        return jsonResponse({
          section: {
            ...casePayload().reportSections[0],
            text: updatedText,
            approvalState: "draft",
            version: 3,
          },
          auditEvent: {
            id: "aud-section-1",
            caseId: "case-3407",
            at: "2026-07-11T10:00:00Z",
            actor: "Miriam Okafor",
            actorRole: "Senior Aviation Counsel",
            action: "report section revised",
            subjectType: "report_section",
            subjectId: section.id,
            previousStatus: "eligible",
            newStatus: "eligible",
            detail: "revised",
            reportEligibilityChanged: false,
          },
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const nav = screen.getByRole("navigation", { name: "Case sections" });
    await user.click(within(nav).getByRole("button", { name: /Reports/ }));
    await user.click(screen.getAllByRole("button", { name: "Revise" })[0]);
    await user.clear(screen.getByLabelText(/Section text/));
    await user.type(screen.getByLabelText(/Section text/), updatedText);
    await user.click(screen.getByRole("button", { name: "Save revision" }));

    expect(
      await screen.findByText(/The latest matter state has been loaded/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/Section text/)).toHaveValue(updatedText);

    await user.click(screen.getByRole("button", { name: "Save revision" }));
    expect(await screen.findByText(updatedText)).toBeInTheDocument();
    expect(submittedVersions).toEqual([1, 2]);
    expect(caseReads).toBe(2);
  });

  it("lets senior counsel approve a draft report section for production", async () => {
    const draftSection = {
      ...sample.reportSections[0],
      approvalState: "draft" as const,
      version: 7,
    };
    let approved = false;
    routeFetch((url, init) => {
      if (url.endsWith("/api/cases/case-3407")) {
        return jsonResponse({
          ...casePayload(),
          reportSections: approved
            ? [{ ...draftSection, approvalState: "approved" as const }, ...sample.reportSections.slice(1)]
            : [draftSection, ...sample.reportSections.slice(1)],
        });
      }
      if (
        url.endsWith(
          `/api/cases/case-3407/report-sections/${draftSection.id}/approve?expectedVersion=7`,
        ) &&
        init?.method === "POST"
      ) {
        approved = true;
        return jsonResponse({
          section: { ...draftSection, approvalState: "approved", version: 8 },
          auditEvent: {
            id: "aud-approve-1",
            caseId: "case-3407",
            at: "2026-07-11T10:00:00Z",
            actor: "Miriam Okafor",
            actorRole: "Senior Aviation Counsel",
            action: "report section approved",
            subjectType: "report_section",
            subjectId: draftSection.id,
            previousStatus: null,
            newStatus: "eligible",
            detail: "approved",
            reportEligibilityChanged: false,
          },
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderApp();
    await screen.findAllByText("Colgan Air Flight 3407");

    const nav = screen.getByRole("navigation", { name: "Case sections" });
    await user.click(within(nav).getByRole("button", { name: /Reports/ }));

    await screen.findByText("Not approved for production");
    await user.click(screen.getByRole("button", { name: "Approve for production" }));

    expect(await screen.findByText("Approved for production")).toBeInTheDocument();
    expect(screen.queryByText("Not approved for production")).not.toBeInTheDocument();
  });

  it("shows a retryable error screen when the case service is unreachable", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    const user = userEvent.setup();
    renderApp();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Case service unreachable/);

    routeFetch();
    await user.click(screen.getByRole("button", { name: "Retry connection" }));
    expect(await screen.findAllByText("Colgan Air Flight 3407")).not.toHaveLength(0);
  });
});
