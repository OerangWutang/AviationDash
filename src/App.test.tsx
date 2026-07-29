import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { StoreProvider } from "./state/store";

function renderApp() {
  return render(
    <StoreProvider>
      <App />
    </StoreProvider>,
  );
}

describe("Atlas Argus conflict review workflow", () => {
  it("shows the case, the default conflict, and both claims with sources", () => {
    renderApp();
    // Case name appears in both the header chip and the sidebar.
    expect(screen.getAllByText("Colgan Air Flight 3407").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("heading", {
        name: /Stall-warning onset time/,
      }),
    ).toBeInTheDocument();
    // Claim A appears on its card and, as default focus, in the Evidence Rail.
    expect(
      screen.getAllByText(/The stick shaker activated at 22:16:27 EST/),
    ).toHaveLength(2);
    expect(
      screen.getByText(/The stall warning began earlier in the approach sequence/),
    ).toBeInTheDocument();
  });

  it("updates the Evidence Rail when a claim is focused", async () => {
    const user = userEvent.setup();
    renderApp();
    const rail = screen.getByRole("complementary", { name: "Evidence Rail" });

    // Default focus is Claim A (FDR readout).
    expect(within(rail).getByText(/FDR Data Readout/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Claim B/ }));
    expect(within(rail).getByText(/Deposition Transcript — R\. Hollis/)).toBeInTheDocument();
    expect(within(rail).getByText(/Chain of custody/i)).toBeInTheDocument();
    expect(within(rail).getByText(/NOT REPORT-SAFE/)).toBeInTheDocument();
  });

  it("gates dispositive decisions on critical conflicts behind senior roles", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.selectOptions(
      screen.getByLabelText("Active reviewer"),
      "rev-natarajan",
    );
    const acceptA = screen.getByRole("radio", { name: /Accept Claim A/ });
    expect(acceptA).toBeDisabled();
    expect(
      screen.getAllByText(/Critical-severity conflicts require Senior Aviation Counsel/)
        .length,
    ).toBeGreaterThan(0);
    // Escalation stays available to everyone. (Matched via its description —
    // the disabled options' explanations also mention "Escalate for senior review".)
    expect(
      screen.getByRole("radio", { name: /Park the conflict for senior review/ }),
    ).toBeEnabled();
  });

  it("requires reasoning, saves a decision, and updates history and report impact", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(screen.getByRole("radio", { name: /Preserve both claims/ }));

    const save = screen.getByRole("button", { name: "Save decision" });
    expect(save).toBeDisabled();
    expect(screen.getByText(/A reasoning note is required/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText(/Reasoning note/),
      "FDR timing is precise but the witness account may reflect the earlier icing encounter; both must be preserved and disclosed.",
    );
    expect(save).toBeEnabled();
    await user.click(save);

    // Confirmation, history, and audit trail all reflect the saved decision.
    expect(screen.getByRole("status")).toHaveTextContent(
      /Decision recorded: Preserve both claims by Miriam Okafor/,
    );
    const history = screen.getByRole("region", { name: "Decision history" });
    expect(within(history).getByText("Preserve both claims")).toBeInTheDocument();
    expect(within(history).getByText(/unresolved → preserved both/)).toBeInTheDocument();

    // Claim statuses updated on the cards.
    expect(screen.getAllByText("preserved").length).toBeGreaterThanOrEqual(2);

    // Report impact: SOE-3 cites Claim A (now preserved) + the already-accepted
    // autopilot claim → usable only with the conflict disclosed.
    const impact = screen.getByRole("region", { name: "Report impact" });
    expect(within(impact).getByText(/Sequence of Events/)).toBeInTheDocument();
    expect(
      within(impact).getAllByText("eligible with disclosure").length,
    ).toBeGreaterThan(0);

    // The unresolved count in the sidebar dropped from 4 to 3.
    const nav = screen.getByRole("navigation", { name: "Case sections" });
    expect(within(nav).getByText("3")).toBeInTheDocument();
  });

  it("drafts a report section with required citations and audits it", async () => {
    const user = userEvent.setup();
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Case sections" });

    await user.click(within(nav).getByRole("button", { name: /Reports/ }));
    await user.click(screen.getByRole("button", { name: "Draft new section" }));

    const editor = screen.getByRole("region", { name: "Section editor" });
    const save = within(editor).getByRole("button", { name: "Save section" });
    expect(save).toBeDisabled();

    await user.type(within(editor).getByLabelText(/Section title/), "Engine Performance");
    await user.type(within(editor).getByLabelText(/Paragraph reference/), "ENG-1");
    await user.type(
      within(editor).getByLabelText(/Section text/),
      "Engine parameters remained within normal ranges throughout the approach [[FDR Data Readout, p. 51].",
    );
    // Still blocked: no citations yet — the doctrine message is shown.
    expect(save).toBeDisabled();
    expect(within(editor).getByText(/cite at least one claim/)).toBeInTheDocument();

    await user.click(within(editor).getByRole("checkbox", { name: "Cite clm-a4" }));
    expect(within(editor).getByText(/Usability with these citations/)).toBeInTheDocument();
    expect(save).toBeEnabled();
    await user.click(save);

    // Editor closes; the new section card appears with live status.
    expect(screen.queryByRole("region", { name: "Section editor" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /Engine Performance/ }),
    ).toBeInTheDocument();
    expect(within(nav).getByText("7")).toBeInTheDocument();

    // Drafting is audited.
    await user.click(within(nav).getByRole("button", { name: /Audit history/ }));
    expect(screen.getAllByText("report section drafted").length).toBeGreaterThan(0);
  });

  it("persists work across a reload and resets to the sample case on demand", async () => {
    const user = userEvent.setup();
    const first = renderApp();

    // Record a decision on CF-1.
    await user.click(screen.getByRole("radio", { name: /Preserve both claims/ }));
    await user.type(
      screen.getByLabelText(/Reasoning note/),
      "Preserving both accounts pending expert reconciliation of the stall timeline.",
    );
    await user.click(screen.getByRole("button", { name: "Save decision" }));
    expect(screen.getByRole("status")).toHaveTextContent(/Decision recorded/);

    // Simulate a reload: unmount and mount a fresh app over the same storage.
    first.unmount();
    renderApp();

    // The decision survived — history shows it and the queue count dropped.
    const history = screen.getByRole("region", { name: "Decision history" });
    expect(within(history).getByText("Preserve both claims")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Case sections" });
    expect(within(nav).getByText("3")).toBeInTheDocument();

    // Reset requires explicit confirmation, then restores the seed.
    await user.click(screen.getByRole("button", { name: /Reset sample case/ }));
    expect(screen.getByText(/Discard all review work/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm reset" }));
    expect(within(nav).getByText("4")).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Decision history" })).getByText(
        /No decisions recorded/,
      ),
    ).toBeInTheDocument();
  });

  it("filters the review queue, including the escalation bucket", async () => {
    const user = userEvent.setup();
    renderApp();
    const queue = screen.getByRole("region", { name: "Review queue" });

    // Escalate CF-1 so the escalated bucket is non-empty.
    await user.click(
      screen.getByRole("radio", { name: /Park the conflict for senior review/ }),
    );
    await user.type(
      screen.getByLabelText(/Reasoning note/),
      "Escalating: stall-timeline dispute needs senior counsel review.",
    );
    await user.click(screen.getByRole("button", { name: "Save decision" }));

    await user.click(within(queue).getByRole("button", { name: /Escalated 1/ }));
    expect(
      within(queue).getByText(/Stall-warning onset time/),
    ).toBeInTheDocument();
    expect(within(queue).queryByText(/Icing severity/)).not.toBeInTheDocument();

    await user.click(within(queue).getByRole("button", { name: /Resolved 1/ }));
    expect(within(queue).getByText(/Autopilot state at upset/)).toBeInTheDocument();
    expect(within(queue).queryByText(/Stall-warning onset time/)).not.toBeInTheDocument();

    await user.click(within(queue).getByRole("button", { name: /Open 3/ }));
    expect(within(queue).getByText(/Icing severity/)).toBeInTheDocument();
  });

  it("extracts a claim from a source and flags a conflict end to end", async () => {
    const user = userEvent.setup();
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Case sections" });

    // Extract a new claim from the first document (FDR readout).
    await user.click(within(nav).getByRole("button", { name: /Documents/ }));
    await user.click(
      screen.getAllByRole("button", { name: "Extract claim from this document" })[0],
    );

    const save = screen.getByRole("button", { name: "Save claim" });
    expect(save).toBeDisabled();
    expect(screen.getByText(/Claim text required/)).toBeInTheDocument();

    await user.type(
      screen.getByLabelText(/Claim text/),
      "Engine torque remained symmetrical through the upset sequence.",
    );
    await user.type(screen.getByLabelText(/Page \/ section reference/), "p. 51, plot 17");
    await user.type(
      screen.getByLabelText(/Supporting quote/),
      "Torque L/R traces track within 2% through 22:16:40.",
    );
    expect(save).toBeEnabled();
    await user.click(save);

    const confirmation = await screen.findByRole("status");
    expect(confirmation).toHaveTextContent(/clm-11/);
    await user.click(screen.getByRole("button", { name: /View in claims ledger/ }));

    // The new claim is in the ledger and focused in the Evidence Rail.
    expect(
      screen.getAllByText(
        "Engine torque remained symmetrical through the upset sequence.",
      ),
    ).toHaveLength(2);

    // Flag a conflict between the new claim and the NTSB icing finding.
    await user.click(screen.getByRole("checkbox", { name: "Select clm-11" }));
    await user.click(screen.getByRole("checkbox", { name: "Select clm-b2" }));
    const flag = screen.getByRole("button", { name: "Flag conflict" });
    expect(flag).toBeDisabled();
    await user.type(
      screen.getByLabelText(/Summary/),
      "Torque symmetry reading conflicts with icing-performance interpretation.",
    );
    expect(flag).toBeEnabled();
    await user.click(flag);

    // The store jumps straight to the new conflict in the review workspace.
    expect(
      screen.getByRole("heading", {
        name: "Torque symmetry reading conflicts with icing-performance interpretation.",
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("disputed").length).toBeGreaterThanOrEqual(2);

    // Both operations are in the audit trail.
    await user.click(within(nav).getByRole("button", { name: /Audit history/ }));
    expect(screen.getAllByText("conflict flagged").length).toBeGreaterThan(0);
    expect(screen.getAllByText("claim extracted").length).toBeGreaterThan(0);
  });

  it("reserves evidence packets for Senior Aviation Counsel", async () => {
    const user = userEvent.setup();
    renderApp();
    await user.selectOptions(screen.getByLabelText("Active reviewer"), "rev-natarajan");

    const nav = screen.getByRole("navigation", { name: "Case sections" });
    await user.click(within(nav).getByRole("button", { name: /Evidence packet/ }));

    expect(
      screen.getByRole("radio", { name: /Internal work-product packet/ }),
    ).toBeDisabled();
    const production = screen.getByRole("radio", { name: /Production packet/ });
    expect(production).toBeDisabled();
    expect(screen.getAllByText(/reserved for Senior Aviation Counsel/)).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Generate packet" })).toBeDisabled();
  });

  it("generates a hash-stamped packet, previews it, and logs it to the audit trail", async () => {
    const user = userEvent.setup();
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Case sections" });
    await user.click(within(nav).getByRole("button", { name: /Evidence packet/ }));

    await user.click(screen.getByRole("button", { name: "Generate packet" }));

    const confirmation = await screen.findByRole("status");
    expect(confirmation).toHaveTextContent(/Packet generated: internal/);
    expect(confirmation).toHaveTextContent(/6 section\(s\) included/);

    // Manifest lists every section with its disposition; preview and export
    // controls are real, working controls.
    const manifest = screen.getByRole("region", { name: "Packet manifest" });
    expect(within(manifest).getByText("Probable Cause")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Download packet \(\.html\)/ }),
    ).toBeEnabled();
    expect(screen.getByRole("button", { name: /Print preview/ })).toBeEnabled();
    // Local mode has no server to render a controlled PDF, so it must not
    // offer one — a dead control here would be worse than its absence.
    expect(screen.queryByRole("button", { name: /Download packet \(\.pdf\)/ })).toBeNull();
    const frame = screen.getByTitle("Evidence packet preview");
    expect(frame).toHaveAttribute("srcdoc", expect.stringContaining("Evidence Packet"));

    // The generation itself is an audit event.
    await user.click(within(nav).getByRole("button", { name: /Audit history/ }));
    expect(screen.getAllByText(/evidence packet generated/).length).toBeGreaterThan(0);
    expect(screen.getByText(/internal packet: 6 section\(s\) included/)).toBeInTheDocument();
  });

  it("navigates to documents, reports, and audit views without dead buttons", async () => {
    const user = userEvent.setup();
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Case sections" });

    await user.click(within(nav).getByRole("button", { name: /Documents/ }));
    expect(
      screen.getByRole("heading", { name: "Documents" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/NTSB Aircraft Accident Report AAR-10\/01/)).toBeInTheDocument();

    await user.click(within(nav).getByRole("button", { name: /Reports/ }));
    expect(screen.getByRole("heading", { name: "Report sections" })).toBeInTheDocument();
    expect(screen.getAllByText(/Report blocked/).length).toBeGreaterThan(0);

    await user.click(within(nav).getByRole("button", { name: /Audit history/ }));
    expect(screen.getByRole("heading", { name: "Audit history" })).toBeInTheDocument();
    expect(screen.getAllByText(/source ingested/).length).toBeGreaterThan(0);
  });
});
