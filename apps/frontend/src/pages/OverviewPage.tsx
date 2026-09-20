import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CircleAlert, RefreshCw } from "lucide-react";
import { LiveSimulationTree } from "../components/LiveSimulationTree";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, Card, Skeleton } from "../components/ui";
import { StrategyOption } from "./DecisionReviewPage";
import { DecisionApprovalDialog } from "../components/DecisionApprovalDialog";
import { getErrorMessage, jsonBody, request, scopedPath } from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { queryKeys } from "../lib/query-keys";
import { useOnlineStatus } from "../lib/hooks";
import type {
  AnalysisResponse,
  JobResponse,
  OverviewResponse,
  Organization,
  ReadinessIssue,
  Strategy,
} from "../lib/types";

export function OverviewPage({ organization }: { organization: Organization }) {
  const [choice, setChoice] = useState<{ analysisId: string; strategyId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const online = useOnlineStatus();
  const [actionError, setActionError] = useState<string>();
  const [analysisJobId, setAnalysisJobId] = useState<string>();
  const [jobIssues, setJobIssues] = useState<OverviewResponse["readinessIssues"]>([]);
  const overview = useQuery<OverviewResponse>({
    queryKey: queryKeys.overview(organization.id),
    queryFn: () => request<OverviewResponse>(scopedPath(organization.id, "/overview")),
    refetchInterval: online ? 60_000 : false,
    refetchIntervalInBackground: false,
    staleTime: 30_000,
  });
  const analysisJob = useQuery<JobResponse>({
    queryKey: queryKeys.job(organization.id, analysisJobId || "none"),
    queryFn: () =>
      request<JobResponse>(
        scopedPath(organization.id, `/jobs/${encodeURIComponent(analysisJobId || "")}`),
      ),
    enabled: Boolean(analysisJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !isTerminalJobStatus(status) ? 2_000 : false;
    },
    staleTime: 0,
  });
  const startAnalysis = useMutation<AnalysisResponse | JobResponse, Error, void>({
    mutationFn: async () => {
      const result = await request<AnalysisResponse | JobResponse>(
        scopedPath(organization.id, "/analyses"),
        jsonBody({ trigger: "manual" }),
      );
      return result;
    },
    onSuccess: async (result) => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.overview(organization.id),
      });
      setJobIssues([]);
      const queuedJobId = "jobId" in result ? result.jobId : undefined;
      if (isPollingJobStatus(result.status)) {
        setAnalysisJobId(queuedJobId || result.id);
        return;
      }
      if (result.status === "needs_inputs") {
        const readinessIssues =
          "result" in result && result.result?.readinessIssues
            ? result.result.readinessIssues
            : "readinessIssues" in result && Array.isArray(result.readinessIssues)
              ? result.readinessIssues
              : [];
        setJobIssues(withReadinessFallback(readinessIssues));
        return;
      }
      const failure = getTerminalFailureMessage(result);
      if (failure) {
        setActionError(failure);
        return;
      }
      const completedAnalysisId =
        "result" in result ? result.result?.analysisId || result.analysisId : result.id;
      if (completedAnalysisId) {
        await queryClient.invalidateQueries({
          queryKey: queryKeys.overview(organization.id),
        });
      } else {
        setActionError("The analysis job completed without publishing an analysis.");
      }
    },
    onError: (error) =>
      setActionError(getErrorMessage(error, "The analysis could not be started.")),
  });

  useEffect(() => {
    const job = analysisJob.data;
    if (!job || !isTerminalJobStatus(job.status)) return;
    const readinessIssues = job.result?.readinessIssues || [];
    if (job.status === "needs_inputs" || readinessIssues.length > 0) {
      setJobIssues(withReadinessFallback(readinessIssues));
      setAnalysisJobId(undefined);
      return;
    }
    const analysisId = job.result?.analysisId || job.analysisId;
    if (analysisId) {
      void queryClient
        .invalidateQueries({ queryKey: queryKeys.overview(organization.id) })
        .then(() => setAnalysisJobId(undefined));
      return;
    }
    if (job.status === "failed" || job.status === "cancelled") {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.overview(organization.id),
      });
      setActionError(
        getJobErrorMessage(job) || "The analysis job did not produce a result.",
      );
      return;
    }
    if (job.status === "needs_login" || job.status === "expired") {
      setAnalysisJobId(undefined);
      setActionError(
        getJobErrorMessage(job) || "Sign in again before running today’s analysis.",
      );
      return;
    }
    setAnalysisJobId(undefined);
    setActionError("The analysis job completed without publishing an analysis.");
  }, [analysisJob.data, organization.id, queryClient]);

  const data = overview.data;
  const analysis = data?.analysis || undefined;
  const latestJob = data?.latestJob || undefined;
  const publishedAt = analysis?.completedAt || data?.generatedAt || data?.asOf;
  const latestJobIsNewer = Boolean(
    latestJob && isJobNewerThanAnalysis(latestJob, publishedAt),
  );
  const polledJob =
    analysisJobId &&
    analysisJob.data &&
    (analysisJob.data.id === analysisJobId || analysisJob.data.jobId === analysisJobId)
      ? analysisJob.data
      : undefined;
  const visibleJob =
    polledJob ||
    (latestJobIsNewer && latestJob && isAttentionJobStatus(latestJob.status)
      ? latestJob
      : undefined);
  const analysisRunning = Boolean(
    startAnalysis.isPending ||
    (analysisJobId &&
      !analysisJob.error &&
      !isTerminalJobStatus(polledJob?.status || "")) ||
    (visibleJob && isPollingJobStatus(visibleJob.status)),
  );

  useEffect(() => {
    if (!latestJob || !isJobNewerThanAnalysis(latestJob, publishedAt)) return;

    const latestJobId = latestJob.id || latestJob.jobId;
    if (latestJobId && isPollingJobStatus(latestJob.status)) {
      setAnalysisJobId((currentJobId) =>
        currentJobId === latestJobId ? currentJobId : latestJobId,
      );
      setJobIssues([]);
      return;
    }

    if (latestJob.status === "needs_inputs") {
      setJobIssues(withReadinessFallback(latestJob.result?.readinessIssues || []));
    } else if (!isPollingJobStatus(latestJob.status)) {
      setJobIssues([]);
    }
  }, [latestJob, publishedAt]);

  const recommended = useMemo(() => selectRecommendedStrategy(analysis), [analysis]);
  const updatedAt = publishedAt;
  const readinessIssues = mergeReadinessIssues(
    data?.readinessIssues || [],
    analysis?.readinessIssues || [],
    jobIssues,
    visibleJob?.result?.readinessIssues || [],
  );
  const readinessBlocked =
    readinessIssues.length > 0 || visibleJob?.status === "needs_inputs";
  const displayReadinessIssues = readinessIssues.length
    ? readinessIssues
    : readinessBlocked
      ? [
          {
            code: "needs_inputs",
            detail: "Review the required planning inputs before running it again.",
          },
        ]
      : [];
  const overviewErrorMessage = overview.error
    ? getErrorMessage(overview.error, "The portfolio overview could not be loaded.")
    : undefined;
  const pollingError =
    analysisJobId && analysisJob.error
      ? getErrorMessage(
          analysisJob.error,
          "Could not check analysis progress. Its outcome is not yet known.",
        )
      : undefined;
  const jobError = pollingError || (visibleJob && getTerminalFailureMessage(visibleJob));
  const decisionError = actionError || jobError || overviewErrorMessage;
  const retryOverview = Boolean(overview.error && !actionError && !jobError);
  const showDecisionCard = Boolean(
    data || overview.error || actionError || visibleJob || displayReadinessIssues.length,
  );
  const handleRun = () => {
    setActionError(undefined);
    setJobIssues([]);
    startAnalysis.mutate();
  };
  const handleRetry = pollingError
    ? () => void analysisJob.refetch()
    : retryOverview
      ? () => void overview.refetch()
      : handleRun;

  return (
    <div className="flex flex-col gap-9">
      <header className="flex flex-col gap-1 border-b border-border/70 pb-4 lg:flex-row lg:items-end lg:justify-between lg:gap-6 lg:pb-8">
        <div className="max-w-2xl space-y-3">
          <h1 className="font-display text-4xl font-medium tracking-[-0.04em] sm:text-5xl">
            Today’s decisions
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-3 lg:justify-end">
          <div className="text-xs text-muted-foreground">
            Last published {updatedAt ? formatDateTime(updatedAt) : "not yet"}
          </div>
        </div>
      </header>

      {!online && (
        <div className="flex items-start gap-3 rounded-2xl border border-amber-300/70 bg-amber-50/80 p-4 text-sm text-amber-900">
          <CircleAlert className="mt-0.5 size-4 shrink-0" />
          <span>
            You are offline. Existing reads remain visible when available. No writes are
            queued; reconnect before changing IntelliQ data.
            {updatedAt && (
              <span className="mt-1 block text-xs text-amber-900/75">
                Showing the in-memory result published {formatDateTime(updatedAt)}.
              </span>
            )}
          </span>
        </div>
      )}
      {data?.stale && !analysisRunning && (
        <div className="flex items-start gap-3 rounded-2xl border border-amber-300/70 bg-amber-50/80 p-4 text-sm text-amber-900">
          <div className="flex min-w-0 items-start gap-3">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>
              This published result is stale. It is labelled with its last source
              timestamp and cannot authorize a change until a fresh analysis is available.
            </span>
          </div>
        </div>
      )}
      {overview.isLoading && !showDecisionCard && <OverviewSkeleton />}
      {choice && (
        <DecisionApprovalDialog
          key={`${choice.analysisId}:${choice.strategyId}`}
          organizationId={organization.id}
          {...choice}
          onClose={() => setChoice(undefined)}
        />
      )}
      {showDecisionCard && (
        <OverviewContent
          data={data}
          analysis={analysis}
          readinessIssues={displayReadinessIssues}
          recommended={recommended}
          onCompare={(analysisId, strategyId) => setChoice({ analysisId, strategyId })}
          onRun={handleRun}
          onRetry={handleRetry}
          running={analysisRunning && !pollingError}
          job={visibleJob}
          online={online}
          retryLabel={retryOverview ? "Retry overview" : "Retry analysis"}
          retrying={retryOverview ? overview.isFetching : startAnalysis.isPending}
          error={decisionError}
        />
      )}
    </div>
  );
}

function OverviewContent({
  data,
  analysis,
  readinessIssues,
  recommended,
  onCompare,
  onRun,
  onRetry,
  running,
  job,
  online,
  retryLabel,
  retrying,
  error,
}: {
  data?: OverviewResponse;
  analysis?: AnalysisResponse;
  readinessIssues: ReadinessIssue[];
  recommended?: Strategy;
  onCompare: (analysisId: string, strategyId: string) => void;
  onRun: () => void;
  onRetry: () => void;
  running: boolean;
  job?: JobResponse;
  online: boolean;
  retryLabel: string;
  retrying: boolean;
  error?: string;
}) {
  const navigate = useNavigate();
  const alternatives = publishedAlternatives(analysis, recommended?.id);
  const currentPlan = analysis?.strategies?.find((strategy) =>
    isCurrentPlanStrategy(strategy),
  );
  const canReview = Boolean(online && !data?.stale && !analysis?.stale && !error);
  const runLabel = getRunLabel(analysis, running);
  const showSummary = Boolean(analysis || (!error && readinessIssues.length === 0));

  return (
    <>
      <section className="space-y-6">
        {running ? (
          <DecisionRunState job={job} />
        ) : (
          <Card className="border-border bg-card p-7 shadow-none sm:p-9">
            <div className="flex flex-col gap-8">
              {analysis && !error && (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h2 className="text-xl font-medium">
                    {alternatives.length ? "Choose an action" : "Simulation complete"}
                  </h2>
                  <Button
                    variant="secondary"
                    onClick={() =>
                      navigate(`/analyses/${encodeURIComponent(analysis.id)}`)
                    }
                  >
                    View simulation
                  </Button>
                </div>
              )}
              {error && (
                <DecisionFailureNotice
                  message={error}
                  onRetry={onRetry}
                  online={online}
                  retryLabel={retryLabel}
                  retrying={retrying}
                />
              )}
              {readinessIssues.length > 0 && (
                <DecisionReadinessNotice
                  issues={readinessIssues}
                  onRetry={onRun}
                  online={online}
                />
              )}
              {showSummary && alternatives.length > 0 && analysis && (
                <div className="space-y-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    {alternatives.map((strategy) => (
                      <StrategyOption
                        key={strategy.id}
                        strategy={strategy}
                        analysis={analysis}
                        selected={false}
                        recommended={strategy.id === recommended?.id}
                        disabled={
                          !canReview ||
                          strategy.feasible === false ||
                          strategy.permitsApproval === false
                        }
                        onSelect={() => onCompare(analysis.id, strategy.id)}
                      />
                    ))}
                  </div>
                </div>
              )}
              {showSummary && alternatives.length === 0 && (
                <div className="space-y-4">
                  {analysis && currentPlan && (
                    <div className="grid gap-3 sm:grid-cols-2">
                      <StrategyOption
                        strategy={currentPlan}
                        analysis={analysis}
                        selected={false}
                        recommended={false}
                        disabled={false}
                        onSelect={() =>
                          navigate(`/analyses/${encodeURIComponent(analysis.id)}`)
                        }
                        readOnly
                      />
                    </div>
                  )}
                  <p className="text-sm leading-6 text-muted-foreground">
                    No additional changes are recommended. You can inspect the evaluated
                    alternatives in the simulation.
                  </p>
                </div>
              )}
              <div className="flex flex-wrap items-center gap-3 pt-2">
                <Button
                  variant={alternatives.length > 0 ? "secondary" : "primary"}
                  onClick={onRun}
                  disabled={!online || running}
                  icon="refresh"
                >
                  {runLabel}
                </Button>
              </div>
            </div>
          </Card>
        )}
      </section>

      {data && <PendingSteps data={data.pendingSteps} />}
      {analysis?.estimatedInputs && analysis.estimatedInputs.length > 0 && (
        <section className="rounded-2xl border border-amber-300/70 bg-amber-50/75 p-5 sm:p-6">
          <div className="flex items-start gap-3">
            <CircleAlert className="mt-0.5 size-4 shrink-0 text-amber-800" />
            <div>
              <p className="eyebrow text-amber-800">Estimated inputs</p>
              <h2 className="mt-2 font-display text-2xl font-medium">
                Some effort fields still use a visible default.
              </h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-amber-900/75">
                These estimates can support a comparison, but they are not reported
                Timecue facts. Confirm them before relying on a forecast.
              </p>
            </div>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {analysis.estimatedInputs.map((input) => (
              <div
                key={`${input.entityId}-${input.field}`}
                className="rounded-xl border border-amber-300/60 bg-background/45 p-4 text-sm"
              >
                <p className="font-medium">{input.field}</p>
                <p className="mt-1 text-xs text-amber-900/70">Entity {input.entityId}</p>
                {input.source && (
                  <p className="mt-2 text-xs text-amber-900/70">Source: {input.source}</p>
                )}
                {input.detail && (
                  <p className="mt-1 text-xs leading-5 text-amber-900/70">
                    {input.detail}
                  </p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

function DecisionFailureNotice({
  message,
  onRetry,
  online,
  retryLabel,
  retrying,
}: {
  message: string;
  onRetry: () => void;
  online: boolean;
  retryLabel: string;
  retrying: boolean;
}) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive/5 p-4"
    >
      <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        <CircleAlert className="size-4" />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <h2 className="text-xl font-medium tracking-tight">Cannot prepare decisions</h2>
        <p className="text-sm leading-6 text-muted-foreground">{message}</p>
      </div>
      <Button
        aria-label={retryLabel}
        title={retryLabel}
        variant="secondary"
        className="size-11 min-h-11 shrink-0 p-0"
        disabled={!online || retrying}
        onClick={onRetry}
      >
        <RefreshCw className="size-4" />
      </Button>
    </div>
  );
}

function DecisionReadinessNotice({
  issues,
  onRetry,
  online,
}: {
  issues: ReadinessIssue[];
  onRetry: () => void;
  online: boolean;
}) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-amber-300/70 bg-amber-50/75 p-4 text-amber-950"
    >
      <div className="flex items-start gap-3">
        <CircleAlert className="mt-0.5 size-4 shrink-0 text-amber-800" />
        <div className="min-w-0">
          <h2 className="text-xl font-medium tracking-tight">Cannot prepare decisions</h2>
          <ul className="mt-2 space-y-1 text-sm leading-6 text-amber-950/80">
            {issues.slice(0, 4).map((issue, index) => (
              <li key={issue.id || `${issue.code}-${index}`}>
                {getReadinessIssueMessage(issue)}
              </li>
            ))}
          </ul>
          {issues.length > 4 && (
            <p className="mt-2 text-xs text-amber-950/70">
              {issues.length - 4} more planning input
              {issues.length - 4 === 1 ? "" : "s"} need review.
            </p>
          )}
          <Button
            variant="quiet"
            aria-label="Retry analysis"
            title="Retry analysis"
            className="mt-4 size-11 min-h-11 p-0"
            onClick={onRetry}
            disabled={!online}
          >
            <RefreshCw className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function PendingSteps({ data }: { data: OverviewResponse["pendingSteps"] }) {
  const navigate = useNavigate();
  if (!data.length)
    return (
      <Button variant="quiet" onClick={() => navigate("/decisions")}>
        Decision history <ArrowRight className="size-4" />
      </Button>
    );
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="mt-2 font-display text-2xl font-medium">
            Recent decisions and open steps
          </h2>
        </div>
        <Button variant="quiet" size="sm" onClick={() => navigate("/decisions")}>
          View decision history <ArrowRight className="size-4" />
        </Button>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        {data.slice(0, 6).map((step) => (
          <Link
            to={
              step.executionId
                ? `/executions/${encodeURIComponent(step.executionId)}`
                : "/decisions"
            }
            key={`${step.executionId}:${step.id}`}
            className="flex items-start gap-3 rounded-2xl border border-border bg-card p-6 transition-colors hover:bg-accent/40"
          >
            <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-xl bg-secondary text-secondary-foreground">
              {step.kind === "manual" ? (
                <CircleAlert className="size-4" />
              ) : (
                <RefreshCw className="size-4" />
              )}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium">
                {step.title || humanize(step.kind) || "Execution step"}
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {step.detail || `Status: ${humanize(step.status)}`}
              </p>
            </div>
            <Badge tone={step.kind === "manual" ? "warning" : "neutral"}>
              {humanize(step.status) || "Pending"}
            </Badge>
          </Link>
        ))}
      </div>
    </section>
  );
}

/** Preserve actionable readiness text from both supported response shapes. */
function getReadinessIssueMessage(issue: ReadinessIssue): string {
  const message =
    "message" in issue && typeof issue.message === "string" ? issue.message : undefined;
  return (
    message ||
    issue.detail ||
    issue.title ||
    (issue.code ? humanize(issue.code) : "A required planning input is missing.")
  );
}

function withReadinessFallback(issues: ReadinessIssue[]): ReadinessIssue[] {
  return issues.length
    ? issues
    : [{ detail: "Review the required planning inputs before running it again." }];
}

function getTerminalFailureMessage(job: JobResponse): string | undefined {
  switch (job.status) {
    case "failed":
      return getJobErrorMessage(job) || "The analysis failed before publishing a result.";
    case "cancelled":
      return getJobErrorMessage(job) || "The analysis was cancelled.";
    case "needs_login":
    case "expired":
      return getJobErrorMessage(job) || "Sign in again before running today’s analysis.";
    default:
      return undefined;
  }
}

/** Deduplicate reasons without discarding distinct project messages. */
function mergeReadinessIssues(...groups: ReadinessIssue[][]): ReadinessIssue[] {
  const seen = new Set<string>();
  const merged: ReadinessIssue[] = [];

  for (const group of groups) {
    for (const issue of group) {
      const key =
        issue.id ||
        [issue.code, issue.projectId, getReadinessIssueMessage(issue)]
          .filter(Boolean)
          .join(":");
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(issue);
    }
  }

  return merged;
}

function isCurrentPlanStrategy(strategy: Strategy): boolean {
  return (
    strategy.id === "baseline" ||
    [
      ...(strategy.strategyRoles || []),
      ...(strategy.labels || []),
      strategy.strategy || "",
    ].some((role) => ["baseline", "unchangedbaseline"].includes(role.toLowerCase()))
  );
}

/** Canonicalize object keys while preserving order within each action's values. */
function canonicalAction(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalAction).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalAction(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

/** Collapse duplicate IDs and explicit identical bundles, retaining the recommended representative. */
function publishedAlternatives(
  analysis?: AnalysisResponse,
  recommendedId?: string,
): Strategy[] {
  const source = analysis?.scenarios?.length
    ? analysis.scenarios
    : analysis?.strategies || [];
  const ordered = [...source].sort(
    (left, right) =>
      Number(right.id === recommendedId) - Number(left.id === recommendedId),
  );
  const ids = new Set<string>();
  const bundles = new Set<string>();
  return ordered.filter((strategy) => {
    if (isCurrentPlanStrategy(strategy) || !strategy.actions?.length) return false;
    let bundle = `id:${strategy.id}`;
    if (strategy.actions?.length) {
      bundle = `actions:${strategy.actions.map(canonicalAction).sort().join("|")}`;
    } else if (strategy.transfer) {
      bundle = `transfer:${canonicalAction(strategy.transfer)}`;
    } else if (strategy.exactChanges?.length) {
      bundle = `changes:${canonicalAction([...strategy.exactChanges].sort())}`;
    } else if (isCurrentPlanStrategy(strategy)) {
      bundle = "current-plan";
    }
    if (ids.has(strategy.id) || bundles.has(bundle)) return false;
    ids.add(strategy.id);
    bundles.add(bundle);
    return true;
  });
}

/** Prefer supplied action text; never turn a policy role or identifier into an action. */
function actionDescription(strategy: Strategy): string {
  if (isCurrentPlanStrategy(strategy) && !strategy.actions?.length)
    return "Keep the current plan";
  if (strategy.exactChanges?.length) return strategy.exactChanges[0];
  const description = strategy.actions
    ?.flatMap((action) =>
      typeof action.description === "string" ? [action.description] : [],
    )
    .join("; ");
  if (description) return description;
  const title = [strategy.title, strategy.name, strategy.label].find(
    (value) =>
      value &&
      value !== strategy.id &&
      !/^(fast|balanced|safe|baseline|unchangedbaseline)$/i.test(value),
  );
  return strategy.summary || strategy.rationale || title || "Review the proposed changes";
}

function selectRecommendedStrategy(analysis?: AnalysisResponse): Strategy | undefined {
  if (!analysis) return undefined;
  const strategies = analysis.scenarios?.length
    ? analysis.scenarios
    : analysis.strategies || [];
  const recommendationId =
    analysis.recommendation?.strategyId || analysis.selectedScenarioId;
  return (
    strategies.find((strategy) => strategy.id === recommendationId) ||
    strategies.find((strategy) => strategy.labels?.includes("recommended")) ||
    strategies.find((strategy) => strategy.rank === 1)
  );
}

function getRunLabel(analysis: AnalysisResponse | undefined, running: boolean): string {
  if (running) return "Preparing analysis…";
  return analysis ? "Update decisions" : "Prepare decisions";
}

function OverviewSkeleton() {
  return <Skeleton className="min-h-72 rounded-xl" />;
}

function DecisionRunState({ job }: { job?: JobResponse }) {
  return (
    <Card className="flex min-h-72 justify-center p-7 sm:p-9">
      <LiveSimulationTree key={job?.id || job?.jobId} job={job} />
    </Card>
  );
}

function isTerminalJobStatus(status: string): boolean {
  return [
    "completed",
    "complete",
    "succeeded",
    "failed",
    "cancelled",
    "needs_inputs",
    "needs_login",
    "expired",
  ].includes(status);
}

function isPollingJobStatus(status: string): boolean {
  return ["queued", "running", "dispatching", "retrying"].includes(status);
}

function isAttentionJobStatus(status: string): boolean {
  return isPollingJobStatus(status) || isTerminalJobStatus(status);
}

function isJobNewerThanAnalysis(job: JobResponse, publishedAt?: string): boolean {
  if (!publishedAt) return true;

  const jobTimestamp = job.completedAt || job.startedAt || job.queuedAt || job.updatedAt;
  if (!jobTimestamp) return true;

  const jobTime = Date.parse(jobTimestamp);
  const publishedTime = Date.parse(publishedAt);
  if (Number.isNaN(jobTime) || Number.isNaN(publishedTime)) return true;
  return jobTime > publishedTime;
}

function getJobTitle(status: string): string {
  switch (status) {
    case "needs_login":
    case "expired":
      return "Sign in again to run today’s analysis";
    case "needs_inputs":
      return "Today’s analysis needs your input";
    case "failed":
      return "Today’s analysis failed";
    case "cancelled":
      return "Today’s analysis was cancelled";
    default:
      return "Preparing today’s analysis";
  }
}

function getJobMessage(job?: JobResponse): string {
  if (!job) return "The bounded simulation is running.";

  const errorMessage = getJobErrorMessage(job);
  if (errorMessage) return errorMessage;
  if (job.status === "needs_inputs") {
    return "Review the missing planning inputs before running it again.";
  }
  return job.stage || "The bounded simulation is running.";
}

function formatJobProgress(progress: number): string {
  const percent = progress <= 1 ? progress * 100 : progress;
  return `${Math.round(percent)}%`;
}

function getJobErrorMessage(job?: JobResponse): string | undefined {
  if (!job) return undefined;
  const rawError = job.actionableError || job.error || job.result?.error;
  return typeof rawError === "string" ? rawError : rawError?.message;
}
