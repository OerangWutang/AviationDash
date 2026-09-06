import { useStore } from "./state/store";
import { Header } from "./components/Header";
import { LoginPage } from "./components/LoginPage";
import { ChangePasswordPage } from "./components/ChangePasswordPage";
import { MfaPage } from "./components/MfaPage";
import { CaseSidebar } from "./components/CaseSidebar";
import { ConflictReviewPanel } from "./components/ConflictReviewPanel";
import { EvidenceRail } from "./components/EvidenceRail";
import { ClaimsView } from "./components/views/ClaimsView";
import { DocumentsView } from "./components/views/DocumentsView";
import { ReportsView } from "./components/views/ReportsView";
import { AuditView } from "./components/views/AuditView";
import { PacketExportView } from "./components/views/PacketExportView";
import { AdminView } from "./components/views/AdminView";
import { NewMatterForm } from "./components/NewMatterForm";

export default function App() {
  const { state, retryBoot } = useStore();

  if (state.bootStatus === "loading") {
    return (
      <div className="boot-screen" role="status">
        <p className="boot-title">Atlas Argus</p>
        <p className="muted">Connecting to the case service…</p>
      </div>
    );
  }
  if (state.bootStatus === "unauthenticated") {
    return <LoginPage />;
  }
  if (state.passwordGate) {
    return <ChangePasswordPage forced={state.passwordGate === "forced"} />;
  }
  if (state.bootStatus === "error") {
    return (
      <div className="boot-screen" role="alert">
        <p className="boot-title">Atlas Argus</p>
        <p className="form-error">{state.bootError}</p>
        <button type="button" className="btn-primary" onClick={retryBoot}>
          Retry connection
        </button>
      </div>
    );
  }

  if (state.dataMode === "server" && state.caseFile.id === "") {
    const canAdminister =
      state.sessionReviewer?.role === "Senior Aviation Counsel";
    return (
      <div className="app-shell">
        <Header />
        <main className="workspace view-stack wide" aria-label="Workspace">
          <header className="view-header">
            <h1>No matters assigned</h1>
            <p className="muted">
              {canAdminister
                ? "Open a matter or administer reviewer accounts."
                : "A Senior Aviation Counsel must grant you access to a matter."}
            </p>
          </header>
          {canAdminister && (
            <section className="panel" aria-label="Matter creation">
              <NewMatterForm />
            </section>
          )}
          {canAdminister && <AdminView />}
        </main>
        {state.mfaGate && <MfaPage />}
      </div>
    );
  }

  const showEvidenceRail = ["conflicts", "claims", "documents", "reports"].includes(
    state.view,
  );

  return (
    <div className="app-shell">
      <Header />
      <div className={`app-main${showEvidenceRail ? "" : " without-rail"}`}>
        <CaseSidebar />
        <main
          key={`workspace-${state.caseFile.id}`}
          className="workspace"
          aria-label="Workspace"
        >
          {state.view === "conflicts" && <ConflictReviewPanel />}
          {state.view === "claims" && <ClaimsView />}
          {state.view === "documents" && <DocumentsView />}
          {state.view === "reports" && <ReportsView />}
          {state.view === "export" && <PacketExportView />}
          {state.view === "audit" && <AuditView />}
          {state.view === "admin" && <AdminView />}
        </main>
        {showEvidenceRail && <EvidenceRail key={`evidence-${state.caseFile.id}`} />}
      </div>
      {state.mfaGate && <MfaPage />}
    </div>
  );
}
