import { useSearchParams } from "react-router-dom";
import { useState } from "react";
import { ArrowLeft, ArrowUpRight, Building2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge as ShadcnBadge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge, EmptyState, ErrorPanel, Skeleton } from "@/components/ui";
import { useApiResource } from "@/lib/hooks";
import { getErrorMessage, jsonBody, request, scopedPath } from "@/lib/api";
import {
  formatDate,
  formatDateTime,
  formatHours,
  formatPercent,
  humanize,
} from "@/lib/format";
import type {
  AnalysisResponse,
  DecisionContract,
  Organization,
  PlanningInputsResponse,
  PortfolioResponse,
  Signal,
  SignalsResponse,
} from "@/lib/types";
import { AnalysisPage } from "./AnalysisPage";

export function WorkspacePage({ organization }: { organization: Organization }) {
  const [params, setParams] = useSearchParams();
  const [historyLimit, setHistoryLimit] = useState(6);
  const portfolio = useApiResource<PortfolioResponse>(
    scopedPath(organization.id, "/portfolio"),
  );
  const history = useApiResource<AnalysisResponse[]>(
    scopedPath(organization.id, "/analyses"),
  );
  const planning = useApiResource<PlanningInputsResponse>(
    scopedPath(organization.id, "/planning-inputs"),
  );
  const projects = portfolio.data?.projects || [];
  const analyses = history.data || [];
  const names = new Map(projects.map((project) => [project.id, project.name]));
  const plannedProjects = new Map(
    planning.data?.projects.map((project) => [project.id, project]),
  );
  const selected = projects.find((project) => project.id === params.get("project"));
  const evidence = useApiResource<SignalsResponse>(
    selected
      ? scopedPath(
          organization.id,
          `/projects/${encodeURIComponent(selected.id)}/signals`,
        )
      : null,
  );
  const decisions = useApiResource<DecisionContract[]>(
    selected ? scopedPath(organization.id, "/decisions") : null,
  );
  const projectAnalyses = analyses.filter(
    (analysis) => analysis.projectId === selected?.id,
  );
  const analysisId = params.get("analysis") || projectAnalyses[0]?.id;
  const tasks = (planning.data?.tasks || []).filter(
    (task) => task.projectId === selected?.id,
  );
  const projectAnalysisIds = new Set(projectAnalyses.map((analysis) => analysis.id));
  const projectDecisions = (decisions.data || []).filter(
    (decision) =>
      decision.targetProjectName === selected?.name ||
      Boolean(decision.analysisId && projectAnalysisIds.has(decision.analysisId)),
  );
  const signals = (
    evidence.data?.signals || [
      ...(evidence.data?.reviewed || []),
      ...(evidence.data?.pending || []),
    ]
  ).map(normalizeSignal);
  const latest = new Map<string, AnalysisResponse>();

  for (const analysis of analyses) {
    if (
      !latest.has(analysis.projectId) &&
      ["complete", "completed"].includes(analysis.status)
    ) {
      latest.set(analysis.projectId, analysis);
    }
  }

  function openProject(projectId: string, savedAnalysisId?: string) {
    setParams(
      savedAnalysisId
        ? { project: projectId, analysis: savedAnalysisId }
        : { project: projectId },
    );
    window.scrollTo({ top: 0 });
  }

  return (
    <div className="space-y-6">
      {portfolio.error && (
        <ErrorPanel
          message={getErrorMessage(portfolio.error)}
          onRetry={() => portfolio.mutate()}
        />
      )}
      {history.error && (
        <ErrorPanel
          title="History unavailable"
          message={getErrorMessage(history.error)}
          onRetry={() => history.mutate()}
        />
      )}
      {planning.error && (
        <ErrorPanel
          title="Planning unavailable"
          message={getErrorMessage(planning.error)}
          onRetry={() => planning.mutate()}
        />
      )}
      {portfolio.isLoading && <Skeleton />}

      {!selected && (
        <>
          <header className="flex flex-wrap items-end justify-between gap-3">
            <div className="space-y-1">
              <h1>Overview</h1>
              <p className="text-muted-foreground">
                Your projects and their latest forecasts.
              </p>
            </div>
            <span className="text-xs text-muted-foreground">
              Updated {formatDateTime(portfolio.data?.asOf)}
            </span>
          </header>
          <Card>
            <CardHeader>
              <CardTitle>Projects</CardTitle>
              <CardDescription>
                Select a project to see its plan and run an analysis.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Project</TableHead>
                    <TableHead>Target</TableHead>
                    <TableHead>Delay risk</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {projects.map((project) => {
                    const analysis = latest.get(project.id);
                    const target =
                      plannedProjects.get(project.id)?.targetFinishAt ||
                      project.targetFinishAt;
                    return (
                      <TableRow key={project.id}>
                        <TableCell>
                          <Button
                            variant="link"
                            className="h-auto justify-start px-0 text-foreground hover:underline hover:underline-offset-4"
                            onClick={() => openProject(project.id)}
                          >
                            <Building2 className="text-muted-foreground" />
                            {project.name}
                          </Button>
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {formatDate(target)}
                        </TableCell>
                        <TableCell>
                          {analysis ? (
                            <ShadcnBadge variant="secondary">
                              {formatPercent(
                                analysis.baselineByProject?.[project.id]
                                  ?.delayProbability,
                              )}
                            </ShadcnBadge>
                          ) : (
                            <span className="text-xs text-muted-foreground">
                              Not analyzed
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              {!portfolio.isLoading && projects.length === 0 && (
                <p className="py-8 text-center text-muted-foreground">
                  No projects available.
                </p>
              )}
            </CardContent>
          </Card>
        </>
      )}

      {selected && (
        <>
          <Button variant="ghost" size="sm" onClick={() => setParams({})}>
            <ArrowLeft />
            All projects
          </Button>
          <header className="flex flex-wrap items-center justify-between gap-4">
            <div className="space-y-1">
              <h1>{selected.name}</h1>
              <p className="text-muted-foreground">
                Target{" "}
                {formatDate(
                  plannedProjects.get(selected.id)?.targetFinishAt ||
                    selected.targetFinishAt,
                )}{" "}
                · {tasks.length} tasks
              </p>
            </div>
          </header>
          <AnalysisPage
            key={`${selected.id}:${analysisId || "new"}`}
            organization={organization}
            projectId={selected.id}
            initialAnalysisId={analysisId}
            onCompleted={() => history.mutate()}
          />
          <Card>
            <CardHeader>
              <CardTitle>Task assumptions</CardTitle>
              <CardDescription>
                Remaining effort and crew size used by the model.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Task</TableHead>
                    <TableHead>Most likely effort</TableHead>
                    <TableHead>Crew</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tasks.map((task) => (
                    <TableRow key={task.id}>
                      <TableCell>{task.title || task.id}</TableCell>
                      <TableCell>
                        {formatHours(task.remainingPersonHours?.mostLikely)}
                      </TableCell>
                      <TableCell>
                        {task.minCrew ?? "—"}–{task.maxCrew ?? "—"} workers
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <div className="grid gap-6 lg:grid-cols-2">
            <ProjectEvidenceSection
              signals={signals}
              isLoading={evidence.isLoading}
              error={evidence.error}
              sourceMode={evidence.data?.sourceMode}
              onRetry={() => evidence.mutate()}
              onReview={async (signal, status) => {
                await request(
                  scopedPath(
                    organization.id,
                    `/signals/${encodeURIComponent(signal.id)}/review`,
                  ),
                  jsonBody({ status }),
                );
                await evidence.mutate();
              }}
            />
            <ProjectDecisionsSection
              decisions={projectDecisions}
              isLoading={decisions.isLoading}
              error={decisions.error}
              onRetry={() => decisions.mutate()}
            />
          </div>
        </>
      )}

      {selected && (
        <Card>
          <CardHeader>
            <CardTitle>Analysis history</CardTitle>
            <CardDescription>
              Saved runs for this project. Open any result to review its forecast.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {history.isLoading && <Skeleton />}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Project</TableHead>
                  <TableHead>Run date</TableHead>
                  <TableHead>Delay risk</TableHead>
                  <TableHead className="text-right">Result</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {projectAnalyses.slice(0, historyLimit).map((analysis) => (
                  <TableRow key={analysis.id}>
                    <TableCell>{names.get(analysis.projectId) || "Project"}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatDateTime(analysis.createdAt || analysis.completedAt)}
                    </TableCell>
                    <TableCell>
                      {formatPercent(
                        analysis.baselineByProject?.[analysis.projectId]
                          ?.delayProbability,
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openProject(analysis.projectId, analysis.id)}
                      >
                        View
                        <ArrowUpRight />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {!history.isLoading && projectAnalyses.length === 0 && (
              <p className="py-8 text-center text-muted-foreground">
                Your first analysis will appear here.
              </p>
            )}
            {projectAnalyses.length > historyLimit && (
              <Button
                variant="outline"
                className="mt-5"
                onClick={() => setHistoryLimit((limit) => limit + 10)}
              >
                Show older analyses
              </Button>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ProjectEvidenceSection({
  signals,
  isLoading,
  error,
  sourceMode,
  onRetry,
  onReview,
}: {
  signals: Signal[];
  isLoading: boolean;
  error?: unknown;
  sourceMode?: "live" | "fixture";
  onRetry: () => void;
  onReview: (signal: Signal, status: "confirmed" | "rejected") => Promise<void>;
}) {
  const [activeSignalId, setActiveSignalId] = useState<string>();
  const [reviewError, setReviewError] = useState<string>();
  const pendingSignals = signals.filter((signal) =>
    ["pending", "proposed"].includes(signal.reviewStatus || "pending"),
  );

  async function review(signal: Signal, status: "confirmed" | "rejected") {
    setActiveSignalId(signal.id);
    setReviewError(undefined);
    try {
      await onReview(signal, status);
    } catch (cause) {
      setReviewError(getErrorMessage(cause, "The evidence review could not be saved."));
    } finally {
      setActiveSignalId(undefined);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Evidence</CardTitle>
            <CardDescription>
              Signals that can change the forecast. {pendingSignals.length} need review.
            </CardDescription>
          </div>
          {sourceMode === "fixture" && <Badge tone="fixture">Demo data</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {Boolean(error) && (
          <ErrorPanel
            title="Evidence unavailable"
            message={getErrorMessage(error, "Signals could not be loaded.")}
            onRetry={onRetry}
          />
        )}
        {reviewError && <ErrorPanel title="Review not saved" message={reviewError} />}
        {isLoading && <Skeleton className="min-h-20" />}
        {!isLoading && !error && signals.length === 0 && (
          <EmptyState
            title="No evidence yet"
            description="No reviewable signals were returned for this project."
          />
        )}
        {signals.slice(0, 3).map((signal) => {
          const status = signal.reviewStatus || "pending";
          const isPending = ["pending", "proposed"].includes(status);
          return (
            <div key={signal.id} className="space-y-3 rounded-lg border p-4">
              <div className="flex items-center justify-between gap-3">
                <Badge
                  tone={
                    status === "confirmed"
                      ? "success"
                      : status === "rejected"
                        ? "danger"
                        : "warning"
                  }
                >
                  {humanize(status)}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {formatDateTime(signal.sourceDate)}
                </span>
              </div>
              <div>
                <p className="font-medium">
                  {signal.title || signal.observation?.kind || "Operational signal"}
                </p>
                <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                  {signal.quote || "No source quote returned."}
                </p>
              </div>
              {isPending && (
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={Boolean(activeSignalId)}
                    onClick={() => review(signal, "rejected")}
                  >
                    Keep outside model
                  </Button>
                  <Button
                    size="sm"
                    disabled={Boolean(activeSignalId)}
                    onClick={() => review(signal, "confirmed")}
                  >
                    {activeSignalId === signal.id ? "Saving…" : "Confirm evidence"}
                  </Button>
                </div>
              )}
            </div>
          );
        })}
        {signals.length > 3 && (
          <p className="text-sm text-muted-foreground">
            Showing the three latest signals.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function ProjectDecisionsSection({
  decisions,
  isLoading,
  error,
  onRetry,
}: {
  decisions: DecisionContract[];
  isLoading: boolean;
  error?: unknown;
  onRetry: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Decisions</CardTitle>
        <CardDescription>
          Recorded recovery choices and their current implementation state.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {Boolean(error) && (
          <ErrorPanel
            title="Decisions unavailable"
            message={getErrorMessage(error, "Decision records could not be loaded.")}
            onRetry={onRetry}
          />
        )}
        {isLoading && <Skeleton className="min-h-20" />}
        {!isLoading && !error && decisions.length === 0 && (
          <EmptyState
            title="No decisions recorded"
            description="Run an analysis and record a recovery strategy to see it here."
          />
        )}
        {decisions.slice(0, 4).map((decision) => (
          <div key={decision.id} className="space-y-2 rounded-lg border p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="font-medium">
                {decision.strategyLabel || decision.message || "Recovery decision"}
              </p>
              <Badge>
                {humanize(decision.implementationStatus || decision.status) || "Recorded"}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              {formatDateTime(decision.approvedAt || decision.createdAt)}
              {decision.donorProjectName ? ` · donor ${decision.donorProjectName}` : ""}
            </p>
            {(decision.managerRationale || decision.rationale) && (
              <p className="text-sm text-muted-foreground">
                {decision.managerRationale || decision.rationale}
              </p>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function normalizeSignal(signal: Signal): Signal {
  const observation = signal.observation;
  return {
    ...signal,
    title: signal.title || observation?.kind,
    quote:
      signal.quote ||
      signal.evidenceQuotes?.[0]?.text ||
      observation?.evidenceQuotes?.[0]?.text,
    reviewStatus:
      signal.reviewStatus || signal.status || observation?.reviewStatus || "pending",
  };
}
