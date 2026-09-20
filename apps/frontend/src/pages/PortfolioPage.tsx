import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  TableCaption,
} from "../components/ui/table";
import { useNavigate } from "react-router-dom";
import {
  Button,
  Card,
  EmptyState,
  ErrorPanel,
  Skeleton,
  StatusDot,
} from "../components/ui";
import { getErrorMessage, scopedPath } from "../lib/api";
import { formatDate, formatDateTime, formatDays, formatPercent } from "../lib/format";
import { useApiResource } from "../lib/hooks";
import type { Organization, PortfolioProject, PortfolioResponse } from "../lib/types";

export function PortfolioPage({ organization }: { organization: Organization }) {
  const navigate = useNavigate();
  const { data, error, isLoading, mutate } = useApiResource<PortfolioResponse>(
    scopedPath(organization.id, "/portfolio"),
  );
  const projects = data?.projects || [];
  const readinessIssues = data?.readinessIssues || [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1>Portfolio</h1>
      </div>
      {error && (
        <ErrorPanel
          title="Portfolio unavailable"
          message={getErrorMessage(error, "The portfolio could not be loaded.")}
          onRetry={() => mutate()}
        />
      )}
      {isLoading && <Skeleton className="table-skeleton" />}
      {data && (
        <>
          <p className="text-muted-foreground">As of {formatDateTime(data.asOf)}</p>
          <Card>
            <div className="w-full overflow-x-auto">
              {projects.length === 0 ? (
                <EmptyState
                  icon="layers"
                  title="No projects returned"
                  description="No projects were returned for this organization."
                />
              ) : (
                <Table>
                  <TableCaption className="sr-only">Project portfolio</TableCaption>
                  <TableHeader>
                    <TableRow>
                      <TableHead scope="col">Project</TableHead>
                      <TableHead scope="col">Target date</TableHead>
                      <TableHead scope="col">Status</TableHead>
                      <TableHead scope="col">Delay risk</TableHead>
                      <TableHead scope="col">
                        <span className="sr-only">Action</span>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {projects.map((project) => (
                      <ProjectRow
                        key={project.id}
                        project={project}
                        onOpen={() =>
                          navigate(`/analysis?project=${encodeURIComponent(project.id)}`)
                        }
                      />
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>
          </Card>
          {readinessIssues.length > 0 && (
            <Card className="flex flex-col gap-6">
              <h2>Readiness issues</h2>
              {readinessIssues.slice(0, 4).map((issue, index) => (
                <div
                  className="flex flex-col gap-6"
                  key={issue.id || `${issue.code}-${index}`}
                >
                  <strong>{issue.title || issue.code || "Planning input"}</strong>
                  <span className="text-muted-foreground">
                    {issue.detail || "Review before analysis."}
                  </span>
                </div>
              ))}
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function ProjectRow({
  project,
  onOpen,
}: {
  project: PortfolioProject;
  onOpen: () => void;
}) {
  const readinessLabel =
    project.readiness === "ready"
      ? "Ready"
      : project.readiness === "blocked"
        ? "Blocked"
        : project.readiness
          ? "Needs inputs"
          : "Not assessed";
  const readinessTone =
    project.readiness === "ready"
      ? "success"
      : project.readiness === "blocked"
        ? "danger"
        : "warning";
  return (
    <TableRow>
      <TableHead scope="row">
        <span className="flex flex-wrap items-center gap-3">
          <StatusDot tone={readinessTone} />
          <span>{project.name}</span>
        </span>
      </TableHead>
      <TableCell>{formatDate(project.targetFinishAt)}</TableCell>
      <TableCell>{readinessLabel}</TableCell>
      <TableCell>
        {formatPercent(project.delayProbability)}{" "}
        <span className="text-muted-foreground">
          ({formatDays(project.expectedPositiveDelayDays)})
        </span>
      </TableCell>
      <TableCell>
        <Button size="sm" onClick={onOpen} aria-label={`Analyze ${project.name}`}>
          Analyze
        </Button>
      </TableCell>
    </TableRow>
  );
}
