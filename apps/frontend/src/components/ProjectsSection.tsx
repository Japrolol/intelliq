import { useRef, useState, type ChangeEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, ChevronDown, FileSpreadsheet, Plus } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { Button, Card, ErrorPanel, Skeleton } from "./ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { formBody, getErrorMessage, request, scopedPath } from "../lib/api";
import { formatDate, formatPercent, outcomeForProject } from "../lib/format";
import type {
  AnalysisResponse,
  ImportPreviewResponse,
  Organization,
  Outcome,
  PortfolioProject,
  Strategy,
} from "../lib/types";

export interface ProjectsSectionProps {
  organization: Organization;
  projects: PortfolioProject[];
  analysis?: AnalysisResponse;
  recommended?: Strategy;
  projectFallbackError?: string;
  loading?: boolean;
  online: boolean;
}

export function ProjectsSection({
  organization,
  projects,
  analysis,
  recommended,
  projectFallbackError,
  loading = false,
  online,
}: ProjectsSectionProps) {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [error, setError] = useState<string>();
  const preview = useMutation<ImportPreviewResponse, Error, File[]>({
    mutationFn: async (selectedFiles) => {
      const body = new FormData();
      selectedFiles.forEach((file) => body.append("files", file, file.name));
      return request<ImportPreviewResponse>(
        scopedPath(organization.id, "/imports/preview"),
        formBody(body),
      );
    },
    onSuccess: (result) => navigate(`/imports/${encodeURIComponent(result.id)}`),
    onError: (cause) =>
      setError(getErrorMessage(cause, "The portfolio files could not be previewed.")),
  });

  function openSpreadsheetPicker() {
    setError(undefined);
    fileInputRef.current?.click();
  }

  function selectFiles(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files || []);
    event.target.value = "";
    if (selected.length === 0) return;
    preview.mutate(selected);
  }

  return (
    <section className="space-y-4" aria-labelledby="projects-heading">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 id="projects-heading" className="font-display text-4xl font-medium">
            Projects
          </h1>
        </div>
        <div className="flex items-center gap-2">
          {preview.isPending && (
            <p className="text-sm text-muted-foreground" aria-live="polite">
              Validating import…
            </p>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.xlsx"
            multiple
            className="sr-only"
            tabIndex={-1}
            aria-label="Import spreadsheet"
            disabled={!online || preview.isPending}
            onChange={selectFiles}
          />
          <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
            <DropdownMenuTrigger asChild>
              <Button
                variant="secondary"
                className="h-11 min-h-11 border-primary/35 bg-card"
                aria-label="Add data"
                disabled={!online || preview.isPending}
              >
                <Plus className="size-4" />
                Add data
                <ChevronDown className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                disabled={!online || preview.isPending}
                onSelect={(event) => {
                  event.preventDefault();
                  openSpreadsheetPicker();
                  setMenuOpen(false);
                }}
              >
                <FileSpreadsheet className="size-4" />
                Import spreadsheet
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      {error && <ErrorPanel title="Import not ready" message={error} />}
      <Card className="overflow-hidden p-0 shadow-none">
        {loading ? (
          <div className="space-y-3 p-6">
            <Skeleton className="h-14 rounded-xl" />
            <Skeleton className="h-14 rounded-xl" />
          </div>
        ) : projects.length > 0 ? (
          <div className="divide-y divide-border/70">
            {projects.map((project) => (
              <ProjectRow
                key={project.id}
                project={project}
                outcome={projectOutcomeForDecision(analysis, recommended, project.id)}
              />
            ))}
          </div>
        ) : (
          <div className="p-6 text-sm text-muted-foreground">
            {projectFallbackError || "No project records were returned."}
          </div>
        )}
      </Card>
      {!online && (
        <p className="text-xs text-muted-foreground">
          Add-data actions remain available to view, but importing requires a connection.
        </p>
      )}
    </section>
  );
}

function ProjectRow({
  project,
  outcome,
}: {
  project: PortfolioProject;
  outcome?: Outcome;
}) {
  const projectPath = `/projects/${encodeURIComponent(project.id)}`;
  const risk =
    outcome?.delayProbability ?? outcome?.delayRisk ?? project.delayProbability;
  const target = project.targetFinishAt || outcome?.targetFinishAt;
  const p50 = outcome?.finishP50 || outcome?.p50;
  const detail =
    project.readinessIssues?.[0]?.detail ||
    outcome?.missingReason ||
    outcome?.censoringReason ||
    (p50 ? `P50 finish ${formatDate(p50)}` : "No project-specific finish published.");

  return (
    <div className="grid gap-4 p-5 sm:grid-cols-[minmax(0,1.5fr)_repeat(3,minmax(110px,0.5fr))_auto] sm:items-center sm:p-6">
      <div className="min-w-0">
        <Link
          to={projectPath}
          className="truncate text-base font-medium text-foreground underline-offset-4 hover:underline"
        >
          {project.name}
        </Link>
        <p className="mt-1 truncate text-sm text-muted-foreground">{detail}</p>
      </div>
      <ProjectFact label="Risk" value={formatPercent(risk)} />
      <ProjectFact label="Target" value={formatDate(target)} />
      <ProjectFact label="P50 finish" value={formatDate(p50)} />
      <Link
        to={projectPath}
        className="inline-flex min-h-10 items-center gap-1 text-sm font-medium text-primary hover:underline"
      >
        Documents &amp; notes
        <ArrowRight className="size-4" />
      </Link>
    </div>
  );
}

function ProjectFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className="mt-1 text-sm font-medium">{value}</p>
    </div>
  );
}

function projectOutcomeForDecision(
  analysis: AnalysisResponse | undefined,
  strategy: Strategy | undefined,
  projectId: string,
): Outcome | undefined {
  return (
    outcomeForProject(strategy?.outcomesByProject, projectId) ||
    outcomeForProject(analysis?.projectOutcomes, projectId) ||
    outcomeForProject(analysis?.baselineByProject, projectId)
  );
}
