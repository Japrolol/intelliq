import { useQuery } from "@tanstack/react-query";
import { ErrorPanel } from "../components/ui";
import { getErrorMessage, request, scopedPath } from "../lib/api";
import { useOnlineStatus } from "../lib/hooks";
import { queryKeys } from "../lib/query-keys";
import type { Organization, OverviewResponse, PortfolioResponse } from "../lib/types";
import { ProjectsSection } from "../components/ProjectsSection";

export function ProjectsPage({ organization }: { organization: Organization }) {
  const online = useOnlineStatus();
  const { data, isLoading, error, refetch } = useQuery<PortfolioResponse>({
    queryKey: ["portfolio", organization.id],
    queryFn: () => request(scopedPath(organization.id, "/portfolio")),
    staleTime: 30_000,
  });
  const { data: overview } = useQuery<OverviewResponse>({
    queryKey: queryKeys.overview(organization.id),
    queryFn: () => request(scopedPath(organization.id, "/overview")),
    staleTime: 30_000,
  });
  return (
    <div className="space-y-6">
      {error && (
        <ErrorPanel
          title="Projects could not be loaded"
          message={getErrorMessage(error, "Could not read your projects from Timecue.")}
          onRetry={() => void refetch()}
        />
      )}
      <ProjectsSection
        organization={organization}
        projects={data?.projects || []}
        analysis={overview?.analysis || undefined}
        loading={isLoading}
        online={online}
      />
      {!online && (
        <p className="text-sm text-muted-foreground">
          Offline. Displayed project data may be outdated; changes require a connection.
        </p>
      )}
    </div>
  );
}
