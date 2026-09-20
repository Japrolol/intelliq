import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  ExternalLink,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Choice, SelectField } from "../components/SelectField";
import { SimulationOptionTree } from "../features/analyses/SimulationOptionTree";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorPanel,
  Skeleton,
  TextInput,
} from "../components/ui";
import { Input } from "../components/ui/input";
import { getErrorMessage, jsonBody, request, scopedPath } from "../lib/api";
import type { Outcome as GeneratedOutcome } from "../lib/generated/api-contracts";
import {
  formatDateTime,
  formatAssumptionValue,
  formatHours,
  humanize,
} from "../lib/format";
import { useOnlineStatus } from "../lib/hooks";
import { queryKeys } from "../lib/query-keys";
import { cn } from "../lib/utils";
import type {
  AnalysisResponse,
  ExecutionOutcomeResponse,
  ExecutionResponse,
  Organization,
  PendingStep,
  PlanningInputsResponse,
  PlanningWorker,
  SourceEvidence,
  Strategy,
} from "../lib/types";

const KNOWLEDGE_FACT_PREFIX = "knowledge:";

function sourceEvidenceHref(source: SourceEvidence): string | undefined {
  const extended = source as SourceEvidence & {
    claimId?: string;
    documentId?: string;
  };
  const documentId = extended.documentId?.trim();
  if (documentId) {
    return `/knowledge?documentId=${encodeURIComponent(documentId)}`;
  }
  const claimId = (extended.claimId || source.id || source.sourceId)?.trim();
  if (!claimId) return undefined;
  const actualClaimId = claimId.startsWith(KNOWLEDGE_FACT_PREFIX)
    ? claimId.slice(KNOWLEDGE_FACT_PREFIX.length)
    : claimId;
  return actualClaimId
    ? `/knowledge?claimId=${encodeURIComponent(actualClaimId)}`
    : undefined;
}

export function AnalysisDetailsPage({ organization }: { organization: Organization }) {
  const { runId } = useParams();
  const online = useOnlineStatus();
  const queryClient = useQueryClient();
  const analysisId = runId || "";
  const [scenarioId, setScenarioId] = useState<string>();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [outcomeMessage, setOutcomeMessage] = useState<string>();
  const analysis = useQuery<AnalysisResponse>({
    queryKey: queryKeys.analysis(organization.id, analysisId),
    queryFn: () =>
      request<AnalysisResponse>(
        scopedPath(organization.id, `/analyses/${encodeURIComponent(analysisId)}`),
      ),
    enabled: Boolean(analysisId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 2_000 : false;
    },
    staleTime: 30_000,
  });
  const executions = useQuery<ExecutionResponse[]>({
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
    enabled: Boolean(analysisId && isComplete(analysis.data) && detailsOpen),
    staleTime: 15_000,
  });
  const planningInputs = useQuery<PlanningInputsResponse>({
    queryKey: ["planning-inputs", organization.id],
    queryFn: () =>
      request<PlanningInputsResponse>(scopedPath(organization.id, "/planning-inputs")),
    enabled: Boolean(analysisId && isComplete(analysis.data) && detailsOpen),
    staleTime: 60_000,
  });
  const result = analysis.data;
  const strategies = useMemo(
    () =>
      (result?.scenarios || result?.strategies || []).filter(
        (strategy, index, all) =>
          all.findIndex((candidate) => candidate.id === strategy.id) === index,
      ),
    [result?.scenarios, result?.strategies],
  );
  const selectedStrategy =
    strategies.find((strategy) => strategy.id === scenarioId) ||
    strategies.find((strategy) => strategy.id === result?.selectedScenarioId) ||
    strategies.find((strategy) => strategy.id === result?.recommendation?.strategyId) ||
    strategies[0];
  const openExecution = executions.data?.find((execution) =>
    execution.steps.some((step) => isOpenStep(step)),
  );
  const outcomeExecution =
    openExecution ||
    executions.data?.find((execution) =>
      execution.steps.some(
        (step) =>
          step.kind === "manual" &&
          step.status === "confirmed" &&
          step.action?.type === "transfer",
      ),
    );
  const confirmedWorkerId = selectedStrategy?.transfer?.workerId;

  useEffect(() => {
    if (!scenarioId && selectedStrategy?.id) setScenarioId(selectedStrategy.id);
  }, [scenarioId, selectedStrategy?.id]);

  const refreshAfterExecution = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.overview(organization.id) });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.analysis(organization.id, analysisId),
    });
    void queryClient.invalidateQueries({
      queryKey: ["executions", organization.id, analysisId],
    });
  };

  const completeStep = useMutation<
    ExecutionResponse,
    Error,
    { executionId: string; stepId: string; note?: string }
  >({
    mutationFn: ({ executionId, stepId, note }) =>
      request<ExecutionResponse>(
        scopedPath(
          organization.id,
          `/executions/${encodeURIComponent(executionId)}/steps/${encodeURIComponent(stepId)}/complete`,
        ),
        jsonBody(note ? { note } : {}),
      ),
    onSuccess: refreshAfterExecution,
  });
  const recordOutcome = useMutation<
    ExecutionOutcomeResponse,
    Error,
    {
      executionId: string;
      workerId: string;
      setupHours: number;
      eventAt: string;
      note?: string;
    }
  >({
    mutationFn: ({ executionId, workerId, setupHours, eventAt, note }) =>
      request<ExecutionOutcomeResponse>(
        scopedPath(
          organization.id,
          `/executions/${encodeURIComponent(executionId)}/outcomes`,
        ),
        jsonBody({
          workerId,
          setupHours,
          eventAt,
          note: note || "",
        } satisfies GeneratedOutcome),
      ),
    onSuccess: (response) => {
      setOutcomeMessage(buildOutcomeMessage(response));
      refreshAfterExecution();
    },
  });
  const cancelExecution = useMutation<
    ExecutionResponse,
    Error,
    { executionId: string; note: string }
  >({
    mutationFn: ({ executionId, note }) =>
      request<ExecutionResponse>(
        scopedPath(
          organization.id,
          `/executions/${encodeURIComponent(executionId)}/cancel`,
        ),
        jsonBody({ note }),
      ),
    onSuccess: refreshAfterExecution,
  });
  const reconcileExecution = useMutation<ExecutionResponse, Error, string>({
    mutationFn: (executionId) =>
      request<ExecutionResponse>(
        scopedPath(
          organization.id,
          `/executions/${encodeURIComponent(executionId)}/reconcile`,
        ),
        jsonBody({}),
      ),
    onSuccess: (response) => {
      setOutcomeMessage(response.message || "Execution checked and refreshed.");
      refreshAfterExecution();
    },
  });

  if (analysis.isLoading) return <Skeleton className="min-h-[620px] rounded-3xl" />;
  if (analysis.error) {
    return (
      <div className="space-y-5">
        <BackLink />
        <ErrorPanel
          title="Analysis unavailable"
          message={getErrorMessage(
            analysis.error,
            "The stored portfolio analysis could not be loaded.",
          )}
          onRetry={() => void analysis.refetch()}
        />
      </div>
    );
  }
  if (!result)
    return (
      <EmptyState
        title="Analysis not found"
        description="This run may have been superseded or is not available in the current organization."
      />
    );
  if (!isComplete(result)) return <AnalysisPending analysis={result} />;

  return (
    <div className="flex flex-col gap-10">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-4">
        <BackLink />
        <div className="flex min-w-0 max-w-full flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <Badge tone={result.status === "failed" ? "danger" : "success"}>
            {humanize(result.status)}
          </Badge>
          <span>{formatDateTime(result.completedAt || result.createdAt)}</span>
        </div>
      </div>

      <header className="flex flex-col gap-6 border-b border-border/70 pb-8 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl space-y-3">
          <h1 className="font-display text-4xl font-medium tracking-[-0.04em] sm:text-5xl">
            Explore what could change
          </h1>
          <p className="text-base leading-7 text-muted-foreground">
            Select an evaluated option to see its actions and the forecast for each
            project.
          </p>
        </div>
      </header>

      <SimulationOptionTree
        key={result.id}
        analysis={result}
        strategies={strategies}
        onPublishedSelect={setScenarioId}
      />

      <div className="flex justify-end">
        <Button
          variant="secondary"
          size="sm"
          aria-expanded={detailsOpen}
          aria-controls="analysis-details"
          onClick={() => setDetailsOpen((open) => !open)}
          className="gap-2"
        >
          {detailsOpen ? "Less detail" : "More details & follow-up"}
          <ChevronDown
            className={cn("size-4 transition-transform", detailsOpen && "rotate-180")}
          />
        </Button>
      </div>

      {detailsOpen && (
        <div id="analysis-details" className="space-y-10">
          <OwnerExplanation analysis={result} strategy={selectedStrategy} />

          <section className="space-y-5">
            <div>
              <p className="eyebrow">Execution and learning</p>
              <h2 className="mt-2 font-display text-2xl font-medium">
                What happened after Apply
              </h2>
            </div>
            {outcomeMessage && (
              <div className="flex items-start gap-3 rounded-2xl border border-primary/25 bg-primary/5 p-4 text-sm">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-primary" />
                <span>{outcomeMessage}</span>
              </div>
            )}
            {executions.error && (
              <ErrorPanel
                title="Execution history unavailable"
                message={getErrorMessage(
                  executions.error,
                  "Execution records could not be loaded.",
                )}
                onRetry={() => void executions.refetch()}
              />
            )}
            {executions.isLoading && <Skeleton className="min-h-32" />}
            {!executions.isLoading && !executions.error && outcomeExecution && (
              <PendingExecution
                execution={outcomeExecution}
                confirmedWorkerId={confirmedWorkerId}
                workers={planningInputs.data?.workers || []}
                online={online}
                completeStep={completeStep}
                recordOutcome={recordOutcome}
                cancelExecution={cancelExecution}
                reconcileExecution={reconcileExecution}
              />
            )}
            {!executions.isLoading && !executions.error && !outcomeExecution && (
              <Card className="p-6">
                <EmptyState
                  title="No open execution steps"
                  description="No pending manual steps or execution outcomes were returned for this run."
                />
              </Card>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function OwnerExplanation({
  analysis,
  strategy,
}: {
  analysis: AnalysisResponse;
  strategy?: Strategy;
}) {
  const confirmed = (analysis.assumptions || []).filter(
    (assumption) => assumption.confirmed,
  );
  return (
    <section className="space-y-5 border-t border-border pt-6" aria-label="Why this plan">
      <h2 className="text-xl">What to consider</h2>
      <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
        {strategy?.rationale ||
          strategy?.summary ||
          "No explanation was returned for this plan."}
      </p>
      {!!strategy?.exactChanges?.length && (
        <ul className="space-y-2 text-sm">
          {strategy.exactChanges.map((change, index) => (
            <li key={index}>{change}</li>
          ))}
        </ul>
      )}
      {confirmed.length > 0 && (
        <div className="space-y-2">
          <h3>Confirmed planning assumptions</h3>
          <ul className="space-y-2 text-sm text-muted-foreground">
            {confirmed.map((assumption, index) => (
              <li key={assumption.id || index}>
                {assumption.label || "Planning assumption"}:{" "}
                {formatAssumptionValue(assumption.value ?? assumption.detail)}
              </li>
            ))}
          </ul>
        </div>
      )}
      {!!analysis.sourceEvidence?.length && (
        <div className="space-y-2">
          <h3>Supporting evidence</h3>
          <ul className="space-y-2 text-sm">
            {analysis.sourceEvidence.map((source, index) => (
              <li key={source.id || index}>
                {sourceEvidenceHref(source) ? (
                  <Link
                    to={sourceEvidenceHref(source) || "/knowledge"}
                    className="flex items-start gap-2 rounded-md text-left hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
                  >
                    <ExternalLink className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    <span>
                      {source.label || "Source record"} ·{" "}
                      {source.confirmed ? "Confirmed" : "Reference only"}
                    </span>
                  </Link>
                ) : (
                  <span className="flex items-start gap-2 text-muted-foreground">
                    <ExternalLink className="mt-0.5 size-4 shrink-0" />
                    <span>{source.label || "Source record"}</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs leading-5 text-muted-foreground">
        Forecasts reflect confirmed planning inputs. Evidence confirmation alone does not
        change a forecast. Unavailable weather or travel context is not evidence that
        conditions are clear.
      </p>
    </section>
  );
}

function PendingExecution({
  execution,
  confirmedWorkerId,
  workers,
  online,
  completeStep,
  recordOutcome,
  cancelExecution,
  reconcileExecution,
}: {
  execution: ExecutionResponse;
  confirmedWorkerId?: string;
  workers: PlanningWorker[];
  online: boolean;
  completeStep: ReturnType<
    typeof useMutation<
      ExecutionResponse,
      Error,
      { executionId: string; stepId: string; note?: string }
    >
  >;
  recordOutcome: ReturnType<
    typeof useMutation<
      ExecutionOutcomeResponse,
      Error,
      {
        executionId: string;
        workerId: string;
        setupHours: number;
        eventAt: string;
        note?: string;
      }
    >
  >;
  cancelExecution: ReturnType<
    typeof useMutation<ExecutionResponse, Error, { executionId: string; note: string }>
  >;
  reconcileExecution: ReturnType<typeof useMutation<ExecutionResponse, Error, string>>;
}) {
  const [note, setNote] = useState("");
  const [workerId, setWorkerId] = useState(confirmedWorkerId || "");
  const [setupHours, setSetupHours] = useState("");
  const [eventAt, setEventAt] = useState("");
  const [outcomeNote, setOutcomeNote] = useState("");
  const [cancelNote, setCancelNote] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  useEffect(() => {
    if (!workerId && workers.length === 1) setWorkerId(workers[0].id);
  }, [workerId, workers]);
  const blockedApiSteps = execution.steps.filter(
    (step) =>
      step.kind === "api" &&
      ["applying", "needs_reconciliation", "blocked"].includes(step.status || ""),
  );
  const needsReconcile =
    ["applying", "needs_reconciliation", "blocked"].includes(execution.status) ||
    blockedApiSteps.length > 0;
  const canCancelRemaining = execution.steps.some((step) =>
    ["pending", "blocked"].includes(step.status || ""),
  );
  const manualSteps = execution.steps.filter(
    (step) => step.kind === "manual" && isOpenStep(step),
  );
  const canRecordOutcome = Boolean(
    workerId &&
    setupHours &&
    eventAt &&
    outcomeNote.trim() &&
    Number.isFinite(Number(setupHours)),
  );
  return (
    <Card className="p-6 sm:p-7">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="eyebrow">Pending execution</p>
          <h2 className="mt-2 font-display text-2xl font-medium">
            {humanize(execution.status)}
          </h2>
        </div>
        <Badge tone="warning">
          {manualSteps.length} open manual step{manualSteps.length === 1 ? "" : "s"}
        </Badge>
      </div>
      <div className="mt-6 space-y-4">
        {manualSteps.map((step) => (
          <div key={step.id} className="rounded-2xl border border-border/70 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="font-medium">{step.title || "Manual operation"}</p>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">
                  {step.detail ||
                    "Complete this step after the manager has verified the real-world action."}
                </p>
              </div>
              <Badge tone="warning">Manual</Badge>
            </div>
            <div className="mt-4 flex flex-col gap-3 sm:flex-row">
              <Input
                aria-label={`Note for ${step.title || "manual step"}`}
                placeholder="Optional manager note"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
              <Button
                size="sm"
                onClick={() =>
                  completeStep.mutate({
                    executionId: execution.id,
                    stepId: step.id,
                    note: note || undefined,
                  })
                }
                disabled={!online || completeStep.isPending}
              >
                {completeStep.isPending ? "Saving…" : "Mark complete"}
              </Button>
            </div>
          </div>
        ))}
        {manualSteps.length === 0 && (
          <p className="text-sm text-muted-foreground">No open manual steps remain.</p>
        )}
      </div>
      <div className="mt-8 border-t border-border/70 pt-6">
        <div className="flex items-start gap-3">
          <div className="flex size-9 items-center justify-center rounded-xl bg-secondary">
            <CheckCircle2 className="size-4 text-primary" />
          </div>
          <div>
            <h3>Record measured setup time</h3>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              This form is available only for a confirmed transfer. Use the measured event
              time; it can inform later forecasts after the transfer is verified.
            </p>
          </div>
        </div>
        {confirmedWorkerId ? (
          <form
            className="mt-5 grid gap-4 sm:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (!canRecordOutcome) return;
              recordOutcome.mutate({
                executionId: execution.id,
                workerId,
                setupHours: Number(setupHours),
                eventAt: new Date(eventAt).toISOString(),
                note: outcomeNote.trim(),
              });
            }}
          >
            {workers.length > 1 ? (
              <label className="space-y-1.5">
                <span className="block text-sm font-medium">Worker</span>
                <SelectField
                  ariaLabel="Worker"
                  value={workerId}
                  onValueChange={setWorkerId}
                  required
                >
                  <Choice value="">Choose the confirmed worker</Choice>
                  {workers.map((worker) => (
                    <Choice key={worker.id} value={worker.id}>
                      {worker.name || "Worker name unavailable"}
                    </Choice>
                  ))}
                </SelectField>
                <span className="block text-xs text-muted-foreground">
                  The confirmed transfer worker is preselected.
                </span>
              </label>
            ) : (
              <TextInput
                label="Confirmed worker"
                value={
                  workers.find((worker) => worker.id === workerId)?.name ||
                  "Worker name unavailable"
                }
                readOnly
              />
            )}
            <TextInput
              label="Measured setup hours"
              type="number"
              min="0"
              step="0.25"
              value={setupHours}
              onChange={(event) => setSetupHours(event.target.value)}
              required
            />
            <TextInput
              className="sm:col-span-2"
              label="Measured event time"
              type="datetime-local"
              value={eventAt}
              onChange={(event) => setEventAt(event.target.value)}
              required
            />
            <TextInput
              className="sm:col-span-2"
              label="Note"
              hint="Required provenance for the measured observation."
              value={outcomeNote}
              onChange={(event) => setOutcomeNote(event.target.value)}
              required
            />
            <div className="sm:col-span-2">
              <Button
                type="submit"
                disabled={!online || !canRecordOutcome || recordOutcome.isPending}
              >
                {recordOutcome.isPending ? "Recording…" : "Record setup outcome"}
              </Button>
            </div>
            {recordOutcome.error && (
              <div className="sm:col-span-2">
                <ErrorPanel
                  title="Outcome not recorded"
                  message={getErrorMessage(
                    recordOutcome.error,
                    "The measured setup outcome could not be saved.",
                  )}
                />
              </div>
            )}
          </form>
        ) : (
          <div className="mt-4 flex items-start gap-2 rounded-xl bg-muted/55 p-4 text-sm text-muted-foreground">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            No confirmed transfer worker is attached to this execution, so setup
            calibration cannot be recorded here.
          </div>
        )}
      </div>
      {needsReconcile && (
        <div className="mt-8 border-t border-border/70 pt-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="eyebrow text-amber-800">Execution check</p>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                The API has not verified every write. Read back the current state before
                deciding whether blocked work should continue or be cancelled.
              </p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => reconcileExecution.mutate(execution.id)}
              disabled={!online || reconcileExecution.isPending}
            >
              {reconcileExecution.isPending ? "Checking…" : "Check execution"}
            </Button>
          </div>
          {blockedApiSteps.length > 0 && (
            <div className="mt-4 space-y-2">
              {blockedApiSteps.map((step) => (
                <div
                  key={step.id}
                  className="rounded-xl border border-amber-300/70 bg-amber-50/60 p-3 text-sm"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{step.title || "API step"}</span>
                    <Badge tone="warning">{humanize(step.status) || "Blocked"}</Badge>
                  </div>
                  {step.detail && (
                    <p className="mt-1 text-xs leading-5 text-amber-900/75">
                      {step.detail}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
          {reconcileExecution.error && (
            <div className="mt-4">
              <ErrorPanel
                title="Execution check failed"
                message={getErrorMessage(
                  reconcileExecution.error,
                  "The current upstream state could not be read back.",
                )}
              />
            </div>
          )}
        </div>
      )}
      {canCancelRemaining && (
        <div className="mt-8 border-t border-border/70 pt-5">
          <details
            open={cancelOpen}
            onToggle={(event) => setCancelOpen(event.currentTarget.open)}
          >
            <summary className="text-sm font-medium text-destructive">
              Cancel remaining steps
            </summary>
            <div className="mt-4 rounded-xl border border-destructive/20 bg-destructive/5 p-4">
              <p className="text-sm leading-6 text-muted-foreground">
                This cancels only pending steps. Applied and confirmed effects stay
                recorded; uncertain writes must be reconciled before cancellation.
              </p>
              <form
                className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end"
                onSubmit={(event) => {
                  event.preventDefault();
                  const note = cancelNote.trim();
                  if (!note) return;
                  cancelExecution.mutate({ executionId: execution.id, note });
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
        </div>
      )}
    </Card>
  );
}

function AnalysisPending({ analysis }: { analysis: AnalysisResponse }) {
  return (
    <div className="space-y-5">
      <BackLink />
      <Card className="p-8">
        <div className="flex items-center gap-3">
          <Badge tone="warning">{humanize(analysis.status)}</Badge>
          <h1 className="font-display text-3xl font-medium">
            Analysis is still being prepared.
          </h1>
        </div>
        <p className="mt-4 max-w-2xl text-sm leading-6 text-muted-foreground">
          The result will appear here when the bounded run completes. No recommendation is
          published until the numerical result is ready.
        </p>
      </Card>
    </div>
  );
}

function isComplete(analysis?: AnalysisResponse): boolean {
  return analysis?.status === "complete" || analysis?.status === "completed";
}

function isOpenStep(step: PendingStep): boolean {
  return ![
    "complete",
    "completed",
    "confirmed",
    "applied",
    "cancelled",
    "failed",
  ].includes(step.status || "");
}

function buildOutcomeMessage(response: ExecutionOutcomeResponse): string {
  const calibration = response.calibration;
  const calibrationDetail = calibrationSummaryText(calibration);
  if (response.message)
    return [response.message, calibrationDetail].filter(Boolean).join(" ");
  const calibrationVersion = response.calibration?.version || response.calibrationVersion;
  if (response.calibrationChanged || response.calibration || calibrationVersion) {
    const later = response.laterForecastAnalysisId
      ? " A later forecast can use this observation."
      : " This observation can inform later forecasts.";
    return ["Measured setup outcome recorded.", calibrationDetail, later]
      .filter(Boolean)
      .join(" ");
  }
  return "Measured setup outcome recorded. The API did not report a calibration change.";
}

function calibrationSummaryText(
  calibration: ExecutionOutcomeResponse["calibration"],
): string {
  if (!calibration) return "";
  const current = calibration.meanHours ?? calibration.updatedMeanHours;
  const prior = calibration.priorMeanHours;
  const hours =
    prior !== undefined && current !== undefined
      ? `${formatHours(prior)} → ${formatHours(current)}`
      : current !== undefined
        ? formatHours(current)
        : undefined;
  const samples =
    typeof calibration.totalSampleCount === "number"
      ? `${calibration.totalSampleCount} observed setup${calibration.totalSampleCount === 1 ? "" : "s"}`
      : undefined;
  if (!hours && !samples) return "";
  return `Observed setup average: ${hours || "updated"}${samples ? ` from ${samples}` : ""}.`;
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
