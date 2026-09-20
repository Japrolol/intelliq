import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Building2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ErrorPanel, Skeleton } from "@/components/ui";
import { Logo } from "@/components/Logo";
import { getErrorMessage } from "@/lib/api";
import { useApiResource } from "@/lib/hooks";
import type { DataMode, Organization, Session } from "@/lib/types";

interface OrganizationPageProps {
  session: Session;
  onOrganizationSelected: (organization: Organization, sourceMode?: DataMode) => void;
  onLogout: () => Promise<void>;
}

export function OrganizationPage({
  session,
  onOrganizationSelected,
  onLogout,
}: OrganizationPageProps) {
  const navigate = useNavigate();
  const {
    data: organizations,
    error: loadError,
    isLoading,
    mutate,
  } = useApiResource<Organization[]>("/organizations");
  const [error, setError] = useState<string>();
  const [signingOut, setSigningOut] = useState(false);

  function openOrganization(organization: Organization) {
    onOrganizationSelected(organization, session.sourceMode);
    navigate("/overview", { replace: true });
  }

  async function signOut() {
    setSigningOut(true);
    setError(undefined);
    try {
      await onLogout();
      navigate("/login", { replace: true });
    } catch (cause) {
      setError(getErrorMessage(cause, "Could not sign out."));
    } finally {
      setSigningOut(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-5 py-12">
      <div className="w-full max-w-lg space-y-6">
        <Card className="gap-8 py-8 sm:py-10">
          <CardHeader className="gap-7 px-7 sm:px-10">
            <Logo />
            <div className="space-y-2">
              <CardTitle className="text-2xl font-semibold tracking-tight">
                Choose your workspace
              </CardTitle>
              <CardDescription>
                Open an organization to see today’s decisions.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="space-y-3 px-7 sm:px-10">
            {loadError && (
              <ErrorPanel message={getErrorMessage(loadError)} onRetry={() => mutate()} />
            )}
            {error && <ErrorPanel message={error} />}
            {isLoading && <Skeleton className="min-h-20" />}
            {organizations?.map((organization) => (
              <Button
                key={organization.id}
                variant="outline"
                className="org-card h-auto min-h-20 w-full justify-start gap-4 whitespace-normal px-5 py-5 text-left"
                onClick={() => openOrganization(organization)}
              >
                <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted">
                  <Building2 className="size-5 text-muted-foreground" />
                </span>
                <span className="flex-1 text-sm font-medium">{organization.name}</span>
                <ArrowRight className="size-4 shrink-0 text-muted-foreground" />
              </Button>
            ))}
            {!isLoading && !loadError && organizations?.length === 0 && (
              <p className="py-6 text-muted-foreground">
                No organizations are available for this account.
              </p>
            )}
          </CardContent>
        </Card>
        <div className="flex flex-wrap items-center justify-between gap-3 px-2 text-xs text-muted-foreground">
          <span>{session.user.email || session.user.name || "Signed in"}</span>
          <Button variant="ghost" disabled={signingOut} onClick={signOut}>
            {signingOut ? "Signing out…" : "Sign out"}
          </Button>
        </div>
      </div>
    </main>
  );
}
