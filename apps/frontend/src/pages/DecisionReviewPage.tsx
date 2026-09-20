import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, CircleAlert, FileCheck2, ShieldCheck } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorPanel, Skeleton } from "../components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { getErrorMessage, jsonBody, request, scopedPath } from "../lib/api";
import type { ApplyChoice as GeneratedApplyChoice } from "../lib/generated/api-contracts";
import {
  formatDateTime,
  formatDays,
  formatPercent,
  humanize,
  projectOutcomeFor,
} from "../lib/format";
import { queryKeys } from "../lib/query-keys";
import { useOnlineStatus } from "../lib/hooks";
import { cn } from "../lib/utils";
import { ChangeCard } from "../components/ChangeCard";
import { describeActions } from "../lib/action-summary";
import type {
  AnalysisResponse,
  ExecutionResponse,
  JobResponse,
  Organization,
  ReviewResponse,
  ReviewStep,
  Strategy,
} from "../lib/types";

export function DecisionReviewPage({ organization }: { organization: Organization }) {
  const { recommendationId } = useParams();
  const [searchParams] = useSearchParams();
  const requestedScenario = searchParams.get("scenario");
  const openedScenario = useRef<string | undefined>(undefined);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const online = useOnlineStatus();
  const [selectedStrategyId, setSelectedStrategyId] = useState<string>(
    requestedScenario || "",
  );
  const [isReviewOpen, setIsReviewOpen] = useState(false);
  const [reviewError, setReviewError] = useState<string>();
  const [review, setReview] = useState<ReviewResponse>();
  const [execution, setExecution] = useState<ExecutionResponse>();
  const [freshAnalysis, setFreshAnalysis] = useState<JobResponse | AnalysisResponse>();
  const applyIdempotencyKeys = useRef(new Map<string, string>());
  const analysisId = recommendationId || "";
  const analysis = useQuery<AnalysisResponse>({
    queryKey: queryKeys.analysis(organization.id, analysisId),
    queryFn: () =>
      request<AnalysisResponse>(
        scopedPath(organization.id, `/analyses/${encodeURIComponent(analysisId)}`),
      ),
    enabled: Boolean(analysisId),
    staleTime: 60_000,
  });
  const executionCheck = useQuery<ExecutionResponse[]>({
    queryKey: ["executions", organization.id, analysisId],
    queryFn: async () => {
      const result = await request<
        ExecutionResponse[] | { executions?: ExecutionResponse[] }
      >(
        scopedPath(
          organization.id,
          `/executions?analysisId=${encodeURIComponent(analysisId)}`,
        ),
      );
      return Array.isArray(result) ? result : result.executions || [];
    },
    enabled: Boolean(analysisId),
    staleTime: 10_000,
  });
  const hasNewerExecution = Boolean(
    analysis.data?.completedAt &&
    executionCheck.data?.some((item) =>
      isAfter(item.updatedAt || item.createdAt, analysis.data?.completedAt),
    ),
  );
  const strategies = useMemo(() => selectStrategies(analysis.data), [analysis.data]);
  const selectedStrategy = strategies.find(
    (strategy) => strategy.id === selectedStrategyId,
  );
  const reviewMutation = useMutation<ReviewResponse, Error, string>({
    mutationFn: (strategyId) =>
      request<ReviewResponse>(
        scopedPath(organization.id, `/analyses/${encodeURIComponent(analysisId)}/review`),
        jsonBody({ strategyId }),
      ),
    onSuccess: (result) => {
      setReview(result);
      setReviewError(undefined);
    },
    onError: (error) =>
      setReviewError(
        getErrorMessage(error, "The exact-change review could not be prepared."),
      ),
  });
  const applyMutation = useMutation<ExecutionResponse, Error, ReviewResponse>({
    mutationFn: (reviewResult) =>
      request<ExecutionResponse>(
        scopedPath(organization.id, `/analyses/${encodeURIComponent(analysisId)}/apply`),
        jsonBody({
          strategyId: reviewResult.strategyId,
          reviewHash: reviewResult.reviewHash,
          idempotencyKey: getApplyIdempotencyKey(
            applyIdempotencyKeys.current,
            reviewResult.reviewHash,
          ),
        } satisfies GeneratedApplyChoice),
      ),
    onSuccess: async (result) => {
      setIsReviewOpen(false);
      setExecution(result);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.overview(organization.id),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.analysis(organization.id, analysisId),
      });
      void queryClient.invalidateQueries({ queryKey: ["executions", organization.id] });
      if (["applied", "partially_applied"].includes(result.status)) {
        try {
          const next = await request<AnalysisResponse | JobResponse>(
            scopedPath(organization.id, "/analyses"),
            jsonBody({
              trigger: "applied_scenario",
              expectedPortfolioRevision: analysis.data?.snapshotRevision,
            }),
          );
          setFreshAnalysis(next);
          void queryClient.invalidateQueries({
            queryKey: queryKeys.overview(organization.id),
          });
        } catch (error) {
          setReviewError(
            `The bundle returned ${humanize(result.status)}, but a fresh analysis could not be started: ${getErrorMessage(error)}`,
          );
        }
      }
    },
    onError: (error) =>
      setReviewError(getErrorMessage(error, "The decision could not be applied.")),
  });

  useEffect(() => {
    if (
      !requestedScenario ||
      !analysis.data ||
      !online ||
      hasNewerExecution ||
      analysis.data.stale
    )
      return;
    const option = strategies.find((item) => item.id === requestedScenario);
    const key = `${analysisId}:${requestedScenario}`;
    if (!option || option.permitsApproval === false || openedScenario.current === key)
      return;
    openedScenario.current = key;
    setSelectedStrategyId(requestedScenario);
    setIsReviewOpen(true);
    reviewMutation.mutate(requestedScenario);
  }, [
    requestedScenario,
    analysisId,
    analysis.data,
    strategies,
    online,
    hasNewerExecution,
    reviewMutation.mutate,
  ]);

  if (analysis.isLoading) return <Skeleton className="min-h-[520px] rounded-3xl" />;
  if (analysis.error) {
    return (
      <div className="space-y-5">
        <BackLink />
        <ErrorPanel
          title="Recommendation unavailable"
          message={getErrorMessage(
            analysis.error,
            "The stored analysis could not be loaded.",
          )}
          onRetry={() => void analysis.refetch()}
        />
      </div>
    );
  }
  if (!analysis.data)
    return (
      <EmptyState
        title="No recommendation selected"
        description="Return to Today and choose a published analysis."
      />
    );

  const currentPlan = strategies.find((strategy) => strategy.id === "baseline");
  return (
    <div className="flex flex-col gap-8">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-4">
        <BackLink />
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span className="max-w-full break-all sm:break-normal">
            Analysis {analysis.data.id}
          </span>
          <span>·</span>
          <span>
            {formatDateTime(analysis.data.completedAt || analysis.data.createdAt)}
          </span>
        </div>
      </div>

      <header className="max-w-3xl space-y-3">
        <p className="eyebrow">Compare and review</p>
        <h1 className="font-display text-4xl font-medium tracking-[-0.04em] sm:text-5xl">
          Choose the move worth authorizing.
        </h1>
        <p className="text-base leading-7 text-muted-foreground">
          Compare the current plan with the evaluated alternatives. Selection changes
          nothing; Apply only sends the frozen bundle you review below.
        </p>
      </header>

      {reviewError && <ErrorPanel title="Review not completed" message={reviewError} />}
      {!online && (
        <div className="rounded-2xl border border-amber-300/70 bg-amber-50/80 p-4 text-sm text-amber-900">
          You are offline. Review can remain visible, but Apply is guarded until
          connectivity returns.
        </div>
      )}
      {hasNewerExecution && (
        <div className="flex items-start gap-3 rounded-2xl border border-amber-300/70 bg-amber-50/80 p-4 text-sm text-amber-900">
          <CircleAlert className="mt-0.5 size-4 shrink-0" />
          <span>
            This analysis is superseded by a newer execution. Review is locked until a new
            analysis is prepared.
          </span>
        </div>
      )}
      {execution && (
        <ExecutionReceipt
          execution={execution}
          organization={organization}
          analysisId={analysisId}
          onExecutionChange={setExecution}
        />
      )}
      {freshAnalysis && (
        <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-primary/25 bg-primary/5 p-4 text-sm">
          <span>
            Fresh analysis{" "}
            {freshAnalysis.status === "queued" || freshAnalysis.status === "running"
              ? "queued"
              : "available"}{" "}
            after Apply.
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => navigate(`/analyses/${encodeURIComponent(freshAnalysis.id)}`)}
          >
            Open fresh analysis
          </Button>
        </div>
      )}

      {strategies.length === 0 ? (
        <Card className="p-8">
          <EmptyState
            title="No automatic changes available"
            description="This analysis has no complete option that can be applied in Timecue. Nothing has been changed."
          />
        </Card>
      ) : (
        <section className="space-y-4">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="eyebrow">Evaluated bundles</p>
              <h2 className="mt-2 font-display text-2xl font-medium">
                Pick one to inspect
              </h2>
            </div>
            <span className="text-xs text-muted-foreground">
              {strategies.length} distinct option{strategies.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {strategies.map((strategy) => (
              <StrategyOption
                key={strategy.id}
                strategy={strategy}
                analysis={analysis.data!}
                selected={strategy.id === selectedStrategy?.id}
                recommended={isRecommended(strategy, analysis.data!)}
                disabled={
                  !online ||
                  hasNewerExecution ||
                  analysis.data.stale === true ||
                  strategy.permitsApproval === false
                }
                onSelect={() => {
                  setSelectedStrategyId(strategy.id);
                  setReview(undefined);
                  setExecution(undefined);
                  setReviewError(undefined);
                  setIsReviewOpen(true);
                  reviewMutation.mutate(strategy.id);
                }}
              />
            ))}
          </div>
          {currentPlan && currentPlan.id !== selectedStrategy?.id && (
            <p className="text-sm text-muted-foreground">
              Current plan is retained as a comparison point; it is not automatically
              changed by this review.
            </p>
          )}
        </section>
      )}

      {selectedStrategy && (
        <Card className="border-primary/20 bg-card p-6 shadow-[0_20px_60px_-42px_rgba(85,56,35,0.4)] sm:p-8">
          <div className="flex flex-wrap items-start justify-between gap-5">
            <div className="flex items-start gap-3">
              <span className="flex size-10 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
                <FileCheck2 className="size-5" />
              </span>
              <div>
                <p className="eyebrow text-primary">Selected option</p>
                <h2 className="mt-1 font-display text-2xl font-medium">
                  {strategyName(selectedStrategy)}
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Review exact changes before any write is authorized.
                </p>
              </div>
            </div>
            <Badge tone="neutral">
              {reviewMutation.isPending ? "Preparing review…" : "Review opened"}
            </Badge>
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            <Metric
              label="Timecue changes"
              value={
                review ? review.steps.filter((step) => step.kind === "api").length : "—"
              }
            />
          </div>
        </Card>
      )}

      <Dialog open={isReviewOpen} onOpenChange={setIsReviewOpen}>
        <DialogContent className="max-h-[90vh] w-[calc(100%-2rem)] max-w-[calc(100%-2rem)] overflow-y-auto p-0 sm:max-w-3xl">
          <DialogHeader className="border-b border-border px-6 py-6 sm:px-8">
            <DialogTitle className="font-display text-2xl">Review changes</DialogTitle>
            <DialogDescription>
              IntelliQ rechecked the source revision. Confirm the exact changes below
              before Apply.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-6 px-6 py-6 sm:px-8">
            {reviewMutation.isPending && <Skeleton className="min-h-36" />}
            {review && (
              <ReviewPreview
                review={review}
                applyPending={applyMutation.isPending}
                onApply={() => applyMutation.mutate(review)}
                disabled={!online || hasNewerExecution}
                onClose={() => setIsReviewOpen(false)}
              />
            )}
            {reviewError && (
              <div className="space-y-4">
                <ErrorPanel title="Preview unavailable" message={reviewError} />
                <Button variant="secondary" onClick={() => navigate("/")}>
                  Open today's decisions
                </Button>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        <ShieldCheck className="size-4 text-primary" />
        <span>Upstream changes and manual work are reported separately after Apply.</span>
        <Link
          to={`/analyses/${encodeURIComponent(analysisId)}`}
          className="font-medium text-primary hover:underline"
        >
          Open full analysis details
        </Link>
      </div>
    </div>
  );
}

export function StrategyOption({
  strategy,
  analysis,
  selected,
  recommended,
  disabled,
  onSelect,
  readOnly = false,
}: {
  strategy: Strategy;
  analysis: AnalysisResponse;
  selected: boolean;
  recommended: boolean;
  disabled: boolean;
  onSelect: () => void;
  readOnly?: boolean;
}) {
  const projects = Object.entries(analysis.baselineByProject ?? {}).map(
    ([id, baseline]) => ({
      id,
      baseline,
      outcome: projectOutcomeFor(strategy.outcomesByProject, id),
    }),
  );
  return (
    <button
      type="button"
      className={cn(
        "rounded-xl border bg-card p-5 text-left transition-colors hover:bg-accent/40 disabled:opacity-50",
        selected && "border-primary bg-primary/5",
      )}
      onClick={onSelect}
      aria-pressed={selected}
      disabled={disabled}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="flex size-9 items-center justify-center rounded-xl bg-secondary text-secondary-foreground">
          {selected || readOnly ? (
            <Check className="size-4" />
          ) : (
            <span className="text-xs font-semibold">{strategy.rank || "—"}</span>
          )}
        </span>
        <div className="flex flex-wrap justify-end gap-2">
          {recommended && <Badge tone="success">Recommended</Badge>}
          {strategy.feasible === false && <Badge tone="danger">Not feasible</Badge>}
        </div>
      </div>
      <h3 className="mt-4 text-lg font-medium">
        {readOnly ? "Keep the current plan" : strategyName(strategy)}
      </h3>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">
        {readOnly
          ? "No better option found among those simulated."
          : strategy.explanation?.summary ||
            (strategy.actions?.length
              ? describeActions(strategy.actions, analysis.projects).join(" ")
              : undefined) ||
            strategy.summary ||
            strategy.rationale ||
            "No explanation was returned for this option."}
      </p>
      <div className="mt-4 space-y-2 border-t pt-4 text-xs">
        <p className="text-muted-foreground">Late-finish risk · current → this option</p>
        {projects.map(({ id, baseline, outcome }) => (
          <div key={id} className="flex items-start justify-between gap-4">
            <span>{baseline.projectName || outcome?.projectName || "Project"}</span>
            <span className="shrink-0 tabular-nums">
              {formatPercent(baseline.delayProbability)} →{" "}
              {formatPercent(outcome?.delayProbability)}
            </span>
          </div>
        ))}
      </div>
      {strategy.transfer && (
        <p className="mt-4 text-xs leading-5 text-muted-foreground">
          {strategy.transfer.workerName || strategy.transfer.workerId || "Worker"} ·{" "}
          {strategy.transfer.fromProjectName ||
            strategy.transfer.fromProjectId ||
            "Donor"}{" "}
          → {strategy.transfer.toProjectName || strategy.transfer.toProjectId || "Target"}
        </p>
      )}
      {strategy.permitsApproval === false && !readOnly && (
        <p className="mt-4 text-xs leading-5 text-destructive">
          {strategy.approvalBlockers?.join(" ") ||
            "This option cannot be approved with the current inputs."}
        </p>
      )}
    </button>
  );
}

export function ReviewPreview({
  review,
  applyPending,
  onApply,
  disabled,
  onClose,
}: {
  review: ReviewResponse;
  applyPending: boolean;
  onApply: () => void;
  disabled: boolean;
  onClose: () => void;
}) {
  const apiSteps = review.steps.filter((step) => step.kind === "api");
  const onSiteSteps = review.steps.filter((step) => step.kind === "manual");
  return (
    <>
      {review.explanation && (
        <section className="space-y-4">
          <p className="text-sm leading-7">{review.explanation.summary}</p>
          <div className="space-y-2 text-sm">
            {review.explanation.comparisons?.map((comparison) => (
              <div key={comparison.projectName} className="flex justify-between gap-4">
                <span>{comparison.projectName}</span>
                <span className="shrink-0 tabular-nums">
                  {formatPercent(comparison.beforeRisk)} →{" "}
                  {formatPercent(comparison.afterRisk)} late-finish risk
                </span>
              </div>
            ))}
          </div>
          <details className="text-sm text-muted-foreground">
            <summary className="cursor-pointer py-2">
              Context used in this forecast
            </summary>
            <ul className="space-y-2 pb-2 leading-6">
              {review.explanation.contextNotes?.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </details>
        </section>
      )}
      {review.warnings.length > 0 && (
        <div className="rounded-2xl border border-amber-300/70 bg-amber-50/80 p-4 text-sm text-amber-900">
          <div className="flex items-center gap-2 font-medium">
            <CircleAlert className="size-4" /> Warnings require your acknowledgement
          </div>
          <ul className="mt-3 space-y-2 pl-5 marker:text-amber-700">
            {review.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
      {apiSteps.length > 0 && (
        <StepGroup
          title="Changes to save in Timecue"
          steps={apiSteps}
          empty="No upstream changes were compiled for this option."
        />
      )}
      {onSiteSteps.length > 0 && (
        <section className="space-y-3">
          <h3>Arrange on site</h3>
          {describeActions(
            onSiteSteps.flatMap((step) => (step.action ? [step.action] : [])),
          ).map((sentence, index) => (
            <ChangeCard key={index} title={sentence}>
              <p className="text-xs text-muted-foreground">
                Tracked as pending in IntelliQ; not automatically changed in Timecue.
              </p>
            </ChangeCard>
          ))}
        </section>
      )}
      <details className="rounded-2xl border border-primary/20 bg-primary/5 text-sm leading-6">
        <summary className="cursor-pointer list-none px-4 py-3 font-medium">
          Audit details
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            Frozen review reference
          </span>
        </summary>
        <div className="border-t border-primary/15 px-4 py-3">
          <p className="text-xs text-muted-foreground">
            Review hash{" "}
            <code className="break-all text-foreground">{review.reviewHash}</code>
          </p>
          <p className="mt-2 text-xs text-muted-foreground">
            This hash freezes the exact preview. A changed source revision must be
            reviewed again.
          </p>
        </div>
      </details>
      <div className="sticky bottom-0 -mx-6 flex flex-col-reverse gap-3 border-t border-border bg-background/95 px-6 py-4 backdrop-blur sm:-mx-8 sm:flex-row sm:justify-end sm:px-8">
        <Button variant="secondary" disabled={applyPending} onClick={onClose}>
          Keep reviewing
        </Button>
        <Button
          onClick={onApply}
          disabled={disabled || applyPending || !review.steps.length}
        >
          {applyPending
            ? "Applying…"
            : apiSteps.length
              ? "Apply changes"
              : "Confirm plan"}
        </Button>
      </div>
    </>
  );
}

function StepGroup({
  title,
  steps,
  empty,
}: {
  title: string;
  steps: ReviewStep[];
  empty: string;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3>{title}</h3>
        <span className="text-xs text-muted-foreground">
          {steps.length} step{steps.length === 1 ? "" : "s"}
        </span>
      </div>
      {steps.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">
          {empty}
        </p>
      ) : (
        <div className="space-y-2">
          {steps.map((step) => (
            <ChangeCard key={step.id} title={step.title || "Update task dates"}>
              {step.detail && (
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  {step.detail}
                </p>
              )}
              {step.action &&
                describeActions([step.action]).map((sentence, index) => (
                  <p key={index} className="text-sm leading-6 text-muted-foreground">
                    {sentence}
                  </p>
                ))}
            </ChangeCard>
          ))}
        </div>
      )}
    </section>
  );
}

export function ExecutionReceipt({
  execution,
  organization,
  analysisId,
  onExecutionChange,
}: {
  execution: ExecutionResponse;
  organization: Organization;
  analysisId: string;
  onExecutionChange: (execution: ExecutionResponse) => void;
}) {
  const queryClient = useQueryClient();
  const online = useOnlineStatus();
  const [stepError, setStepError] = useState<string>();
  const [activeStep, setActiveStep] = useState<string>();
  const [cancelNote, setCancelNote] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  const completeStep = useMutation<ExecutionResponse, Error, string>({
    mutationFn: (stepId) =>
      request<ExecutionResponse>(
        scopedPath(
          organization.id,
          `/executions/${encodeURIComponent(execution.id)}/steps/${encodeURIComponent(stepId)}/complete`,
        ),
        jsonBody({}),
      ),
    onSuccess: (result) => {
      onExecutionChange(result);
      setActiveStep(undefined);
      setStepError(undefined);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.overview(organization.id),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.analysis(organization.id, analysisId),
      });
      void queryClient.invalidateQueries({ queryKey: ["executions", organization.id] });
    },
    onError: (error) => {
      setActiveStep(undefined);
      setStepError(getErrorMessage(error, "The manual step could not be completed."));
    },
  });
  const cancelExecution = useMutation<ExecutionResponse, Error, { note: string }>({
    mutationFn: ({ note }) =>
      request<ExecutionResponse>(
        scopedPath(
          organization.id,
          `/executions/${encodeURIComponent(execution.id)}/cancel`,
        ),
        jsonBody({ note }),
      ),
    onSuccess: (result) => {
      onExecutionChange(result);
      setCancelOpen(false);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.overview(organization.id),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.analysis(organization.id, analysisId),
      });
      void queryClient.invalidateQueries({
        queryKey: ["executions", organization.id],
      });
    },
  });
  const manualSteps = execution.steps.filter(
    (step) =>
      step.kind === "manual" &&
      !["completed", "complete", "confirmed", "cancelled", "applied"].includes(
        step.status || "",
      ),
  );
  const retryExecution = useMutation<ExecutionResponse, Error>({
    mutationFn: () =>
      request<ExecutionResponse>(
        scopedPath(
          organization.id,
          `/executions/${encodeURIComponent(execution.id)}/reconcile`,
        ),
        jsonBody({}),
      ),
    onSuccess: (result) => {
      onExecutionChange(result);
      void queryClient.invalidateQueries({ queryKey: ["executions", organization.id] });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.overview(organization.id),
      });
    },
  });
  const hasUnfinishedWrites = execution.steps.some(
    (step) =>
      step.kind === "api" &&
      ["pending", "blocked", "needs_reconciliation", "failed"].includes(
        step.status || "",
      ),
  );
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="mt-2 font-display text-2xl font-medium">
            {humanize(execution.status)}
          </h2>
        </div>
        <Badge
          tone={
            execution.status === "applied"
              ? "success"
              : execution.status === "failed"
                ? "danger"
                : "warning"
          }
        >
          {humanize(execution.status)}
        </Badge>
      </div>
      {execution.message && (
        <p className="text-sm leading-6 text-muted-foreground">{execution.message}</p>
      )}
      {stepError && <ErrorPanel title="Manual step not completed" message={stepError} />}
      {hasUnfinishedWrites && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Some Timecue changes are not verified. Retrying checks their current state
            before continuing the approved changes.
          </p>
          <Button
            variant="secondary"
            disabled={!online || retryExecution.isPending || completeStep.isPending}
            onClick={() => retryExecution.mutate()}
          >
            {retryExecution.isPending
              ? "Checking changes…"
              : "Retry remaining Timecue changes"}
          </Button>
          {retryExecution.error && (
            <ErrorPanel
              title="Could not continue changes"
              message={getErrorMessage(retryExecution.error)}
            />
          )}
        </div>
      )}
      {manualSteps.length > 0 && (
        <div className="grid gap-3 lg:grid-cols-2">
          {manualSteps.map((step) => (
            <Card key={step.id} className="p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">{step.title || "Manual step"}</p>
                  {step.detail && (
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      {step.detail}
                    </p>
                  )}
                </div>
                <Badge tone="warning">Manual</Badge>
              </div>
              <Button
                className="mt-5 w-full"
                size="sm"
                onClick={() => {
                  setActiveStep(step.id);
                  completeStep.mutate(step.id);
                }}
                disabled={!online || completeStep.isPending || step.status !== "pending"}
              >
                {activeStep === step.id ? "Saving…" : "Mark as resolved"}
              </Button>
            </Card>
          ))}
        </div>
      )}
      {manualSteps.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No open manual steps were returned by the execution.
        </p>
      )}
      {manualSteps.length > 0 && (
        <details
          open={cancelOpen}
          onToggle={(event) => setCancelOpen(event.currentTarget.open)}
          className="border-t border-border/70 pt-4"
        >
          <summary className="text-sm font-medium text-destructive">
            Cancel remaining steps
          </summary>
          <div className="mt-4 rounded-xl border border-destructive/20 bg-destructive/5 p-4">
            <p className="text-sm leading-6 text-muted-foreground">
              Pending steps will be cancelled; applied and confirmed effects remain. The
              API will refuse cancellation while a write is uncertain.
            </p>
            <form
              className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end"
              onSubmit={(event) => {
                event.preventDefault();
                const note = cancelNote.trim();
                if (note) cancelExecution.mutate({ note });
              }}
            >
              <label className="min-w-0 flex-1">
                <span className="mb-1.5 block text-xs font-medium">Reason</span>
                <Input
                  value={cancelNote}
                  onChange={(event) => setCancelNote(event.target.value)}
                  placeholder="Why should the remaining work stop?"
                  required
                />
              </label>
              <Button
                type="submit"
                variant="danger"
                size="sm"
                disabled={!online || !cancelNote.trim() || cancelExecution.isPending}
              >
                {cancelExecution.isPending ? "Cancelling…" : "Cancel remaining"}
              </Button>
            </form>
            {cancelExecution.error && (
              <div className="mt-4">
                <ErrorPanel
                  title="Remaining steps not cancelled"
                  message={getErrorMessage(
                    cancelExecution.error,
                    "The execution could not be cancelled.",
                  )}
                />
              </div>
            )}
          </div>
        </details>
      )}
    </section>
  );
}

function selectStrategies(analysis?: AnalysisResponse): Strategy[] {
  if (!analysis) return [];
  const all = analysis.scenarios || analysis.strategies || [];
  const seen = new Set<string>();
  const distinct = all.filter((strategy) => {
    if (strategy.id === "baseline" || !strategy.actions?.length) return false;
    if (seen.has(strategy.id)) return false;
    seen.add(strategy.id);
    return true;
  });
  return [...distinct].sort((left, right) => {
    const leftRecommended = isRecommended(left, analysis);
    const rightRecommended = isRecommended(right, analysis);
    if (leftRecommended !== rightRecommended) return leftRecommended ? -1 : 1;
    if (left.id === "baseline") return 1;
    if (right.id === "baseline") return -1;
    return (
      (left.rank || Number.MAX_SAFE_INTEGER) - (right.rank || Number.MAX_SAFE_INTEGER)
    );
  });
}

function isRecommended(strategy: Strategy, analysis: AnalysisResponse): boolean {
  return (
    strategy.id === analysis.recommendation?.strategyId ||
    strategy.id === analysis.selectedScenarioId ||
    strategy.labels?.includes("recommended") === true ||
    strategy.rank === 1
  );
}

function strategyName(strategy: Strategy): string {
  return (
    strategy.strategyName ||
    strategy.title ||
    strategy.label ||
    strategy.name ||
    humanize(strategy.id) ||
    "Portfolio option"
  );
}

function countManualActions(strategy: Strategy): number {
  return (
    strategy.actions?.filter((action) => {
      const type = typeof action.type === "string" ? action.type : "";
      const capability = typeof action.capability === "string" ? action.capability : "";
      return (
        action.kind === "manual" ||
        capability === "manual" ||
        ["transfer", "priority", "resequence"].includes(type)
      );
    }).length || 0
  );
}

function Metric({
  label,
  value,
  compact = false,
}: {
  label: string;
  value: string | number;
  compact?: boolean;
}) {
  return (
    <div
      className={
        compact ? "space-y-1" : "rounded-xl border border-border/70 bg-background/50 p-4"
      }
    >
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`${compact ? "mt-1" : "mt-2 text-lg"} font-medium`}>{value}</p>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/overview"
      className="inline-flex min-h-11 items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" /> Back to Today
    </Link>
  );
}

function createIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `intelliq-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function getApplyIdempotencyKey(keys: Map<string, string>, reviewHash: string): string {
  const existing = keys.get(reviewHash);
  if (existing) return existing;
  const next = createIdempotencyKey();
  keys.set(reviewHash, next);
  return next;
}

function isAfter(candidate?: string, reference?: string): boolean {
  if (!candidate || !reference) return false;
  const candidateTime = Date.parse(candidate);
  const referenceTime = Date.parse(reference);
  return (
    Number.isFinite(candidateTime) &&
    Number.isFinite(referenceTime) &&
    candidateTime > referenceTime
  );
}
