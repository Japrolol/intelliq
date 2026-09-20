import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Skeleton } from "../components/ui";
import { Logo } from "../components/Logo";
import { ApiError, getErrorMessage, request } from "../lib/api";
import type { DataMode, Organization, RuntimeConfig, Session } from "../lib/types";
import { Shell } from "./Shell";
import { DecisionsPage } from "../pages/DecisionsPage";
import { LoginPage } from "../pages/LoginPage";
import { OrganizationPage } from "../pages/OrganizationPage";
import { ExecutionHistoryPage } from "../pages/ExecutionHistoryPage";
import { OverviewPage } from "../pages/OverviewPage";
import { DecisionReviewPage } from "../pages/DecisionReviewPage";
import { AnalysisDetailsPage } from "../pages/AnalysisDetailsPage";
import { ImportsPage } from "../pages/ImportsPage";
import { KnowledgePage } from "../pages/KnowledgePage";
import { ProjectPage } from "../pages/ProjectPage";
import { ProjectsPage } from "../pages/ProjectsPage";

type AuthStatus = "checking" | "signed_out" | "signed_in" | "error";
const SELECTED_ORGANIZATION_KEY = "intelliq:selected-organization-id";

function readSelectedOrganizationId(): string | undefined {
  try {
    return window.sessionStorage.getItem(SELECTED_ORGANIZATION_KEY) || undefined;
  } catch {
    return undefined;
  }
}

function writeSelectedOrganizationId(value?: string) {
  try {
    if (value) window.sessionStorage.setItem(SELECTED_ORGANIZATION_KEY, value);
    else window.sessionStorage.removeItem(SELECTED_ORGANIZATION_KEY);
  } catch {
    // Session storage is a convenience for navigation, never an auth source.
  }
}

export default function App() {
  const [status, setStatus] = useState<AuthStatus>("checking");
  const [session, setSession] = useState<Session>();
  const [authError, setAuthError] = useState<string>();
  const [runtime, setRuntime] = useState<RuntimeConfig>();
  const [runtimeStatus, setRuntimeStatus] = useState<"checking" | "ready" | "error">(
    "checking",
  );
  const [runtimeError, setRuntimeError] = useState<string>();
  const [organization, setOrganization] = useState<Organization>();
  const [sourceMode, setSourceMode] = useState<DataMode>();
  const [forceOrganizationPicker, setForceOrganizationPicker] = useState(false);

  const refreshSession = useCallback(async () => {
    setStatus("checking");
    setAuthError(undefined);
    try {
      const nextSession = await request<Session>("/auth/me");
      setSession(nextSession);
      setSourceMode(nextSession.synthetic ? "fixture" : nextSession.sourceMode);
      setStatus("signed_in");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setStatus("signed_out");
        return;
      }
      setStatus("error");
      setAuthError(
        getErrorMessage(error, "IntelliQ could not verify the current session."),
      );
    }
  }, []);

  useEffect(() => {
    void refreshSession();
    void request<RuntimeConfig>("/runtime")
      .then((config) => {
        setRuntime(config);
        setRuntimeStatus("ready");
      })
      .catch((error) => {
        setRuntimeError(
          error instanceof ApiError && error.status === 404
            ? "The service is unavailable. Please try again."
            : getErrorMessage(error, "Could not connect to the service."),
        );
        setRuntimeStatus("error");
      });
  }, [refreshSession]);

  useEffect(() => {
    if (status !== "signed_in" || !session || organization || forceOrganizationPicker)
      return;
    const selectedOrganizationId = readSelectedOrganizationId();

    void request<Organization[]>("/organizations")
      .then((organizations) => {
        const restored = selectedOrganizationId
          ? organizations.find((candidate) => candidate.id === selectedOrganizationId)
          : undefined;
        if (restored) {
          setOrganization(restored);
          setSourceMode(session.synthetic ? "fixture" : session.sourceMode);
        } else if (organizations.length === 1) {
          setOrganization(organizations[0]);
          writeSelectedOrganizationId(organizations[0].id);
          setSourceMode(session.synthetic ? "fixture" : session.sourceMode);
        } else {
          writeSelectedOrganizationId();
        }
      })
      .catch(() => {
        // The organization picker remains the safe fallback when the authorized
        // list cannot be revalidated after a reload.
      });
  }, [forceOrganizationPicker, organization, session, status]);

  async function logout() {
    try {
      await request("/auth/logout", { method: "POST" });
    } catch (error) {
      // A 401 proves the upstream/local session is already invalid. Other
      // failures must not create a false sign-out while the cookie remains.
      if (!(error instanceof ApiError && error.status === 401)) throw error;
    }
    setSession(undefined);
    setOrganization(undefined);
    setForceOrganizationPicker(false);
    writeSelectedOrganizationId();
    setSourceMode(undefined);
    setStatus("signed_out");
  }

  function handleSignedIn(nextSession: Session) {
    setSession(nextSession);
    setSourceMode(nextSession.synthetic ? "fixture" : nextSession.sourceMode);
    setStatus("signed_in");
  }

  function handleOrganizationSelected(nextOrganization: Organization, mode?: DataMode) {
    setOrganization(nextOrganization);
    setForceOrganizationPicker(false);
    writeSelectedOrganizationId(nextOrganization.id);
    setSourceMode(mode);
  }

  function switchOrganization() {
    setOrganization(undefined);
    setForceOrganizationPicker(true);
    writeSelectedOrganizationId();
  }

  if (status === "checking" || runtimeStatus === "checking") return <LoadingScreen />;
  if (status === "error")
    return (
      <PublicFailure
        title="Session verification unavailable"
        message={authError || "Session verification failed."}
        onRetry={refreshSession}
      />
    );
  if (status === "signed_out" || !session) {
    if (!runtime)
      return (
        <PublicFailure
          title="Unable to connect"
          message={runtimeError || "Could not connect to the service. Please try again."}
          onRetry={() => window.location.reload()}
        />
      );
    return (
      <Routes>
        <Route
          path="*"
          element={
            <LoginPage
              runtime={runtime}
              onSignedIn={handleSignedIn}
              initialError={authError}
            />
          }
        />
      </Routes>
    );
  }
  if (!organization)
    return (
      <Routes>
        <Route
          path="*"
          element={
            <OrganizationPage
              session={session}
              onLogout={logout}
              onOrganizationSelected={handleOrganizationSelected}
            />
          }
        />
      </Routes>
    );

  return (
    <Routes>
      <Route
        element={
          <Shell
            user={session.user}
            organization={organization}
            sourceMode={sourceMode}
            onLogout={logout}
            onSwitchOrganization={switchOrganization}
          />
        }
      >
        <Route path="/overview" element={<OverviewPage organization={organization} />} />
        <Route
          path="/projects/:projectId"
          element={<ProjectPage organization={organization} />}
        />
        <Route path="/projects" element={<ProjectsPage organization={organization} />} />
        <Route
          path="/decisions/:recommendationId"
          element={<DecisionReviewPage organization={organization} />}
        />
        <Route
          path="/analyses/:runId"
          element={<AnalysisDetailsPage organization={organization} />}
        />
        <Route
          path="/imports/new"
          element={<ImportsPage organization={organization} />}
        />
        <Route
          path="/imports/:importId"
          element={<ImportsPage organization={organization} />}
        />
        <Route
          path="/knowledge"
          element={<KnowledgePage organization={organization} />}
        />
        <Route path="/settings" element={<Navigate to="/overview" replace />} />
        <Route path="/portfolio" element={<Navigate to="/projects" replace />} />
        <Route path="/analysis" element={<Navigate to="/overview" replace />} />
        <Route path="/evidence" element={<Navigate to="/knowledge" replace />} />
        <Route
          path="/decisions"
          element={<ExecutionHistoryPage organization={organization} />}
        />
        <Route
          path="/executions/:executionId"
          element={<ExecutionHistoryPage organization={organization} />}
        />
        <Route
          path="/history/decisions/:decisionId"
          element={
            <DecisionsPage
              organization={organization}
              sourceMode={sourceMode}
              actorId={session.user.id}
            />
          }
        />
        <Route path="/" element={<Navigate to="/overview" replace />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  );
}

function LoadingScreen() {
  return (
    <div
      className="flex min-h-screen flex-col items-center justify-center gap-6"
      aria-label="Loading workspace"
    >
      <Logo />
      <Skeleton className="h-1 w-48" />
    </div>
  );
}

function PublicFailure({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 p-6">
      <Logo />
      <div className="flex max-w-sm flex-col gap-4 rounded-xl border bg-card p-6">
        <h1>{title}</h1>
        <p>{message}</p>
        <button
          className="inline-flex items-center justify-center rounded-md border bg-card px-4 py-2 text-sm font-medium"
          onClick={onRetry}
        >
          Try again
        </button>
      </div>
    </div>
  );
}
