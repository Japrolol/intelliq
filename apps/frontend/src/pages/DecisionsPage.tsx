import { Textarea } from "../components/ui/textarea";
import { Checkbox } from "../components/ui/checkbox";
import { Input } from "../components/ui/input";
import { SelectField, Choice } from "../components/SelectField";
import { FormEvent, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Icon } from "../components/Icon";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorPanel,
  Skeleton,
  SourceBadge,
} from "../components/ui";
import { BackToWorkspace } from "../components/BackToWorkspace";
import { getErrorMessage, jsonBody, request, scopedPath } from "../lib/api";
import {
  formatDateTime,
  formatDays,
  formatHours,
  formatPercent,
  humanize,
} from "../lib/format";
import { useApiResource } from "../lib/hooks";
import type {
  CalibrationResponse,
  CalibrationVersion,
  CheckpointComparator,
  CheckpointEvaluation,
  CheckpointState,
  DataMode,
  DecisionContract,
  DecisionImplementationEvent,
  DecisionObservation,
  ImplementationState,
  Organization,
} from "../lib/types";

const DECISION_IMPLEMENTATION_RESOURCE = "implementation";
const DECISION_OBSERVATIONS_RESOURCE = "observations";
const DECISION_CHECKPOINTS_RESOURCE = "checkpoints/check";
const CALIBRATION_SETUP_HOURS_RESOURCE = "/calibration/setup-hours";
const DEFAULT_CHECKPOINT_ID = "remaining-effort-check";

const implementationStates: Array<{
  value: ImplementationState;
  label: string;
}> = [
  { value: "reported", label: "Reported as started" },
  { value: "partially_implemented", label: "Partially implemented" },
  { value: "implemented", label: "Implemented by the manager" },
  { value: "not_implemented", label: "Not implemented" },
];

export function DecisionsPage({
  organization,
  sourceMode,
  actorId,
}: {
  organization: Organization;
  sourceMode?: DataMode;
  actorId: string;
}) {
  const { decisionId } = useParams();
  return decisionId ? (
    <DecisionDetail
      organization={organization}
      decisionId={decisionId}
      sourceMode={sourceMode}
      actorId={actorId}
    />
  ) : (
    <DecisionHistory organization={organization} sourceMode={sourceMode} />
  );
}

function DecisionHistory({
  organization,
  sourceMode,
}: {
  organization: Organization;
  sourceMode?: DataMode;
}) {
  const navigate = useNavigate();
  const { data, error, isLoading, mutate } = useApiResource<DecisionContract[]>(
    scopedPath(organization.id, "/decisions"),
  );
  const decisions = Array.isArray(data) ? data : [];
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <BackToWorkspace />
        <h1>Decisions</h1>
      </div>
      {error && (
        <ErrorPanel
          title="Decision history unavailable"
          message={getErrorMessage(error, "Decision records could not be loaded.")}
          onRetry={() => mutate()}
        />
      )}
      {isLoading && <Skeleton className="decision-skeleton" />}
      {data && (
        <Card className="flex flex-col gap-6">
          <div className="flex flex-wrap items-center gap-3">
            <h2>Recorded decisions</h2>
            <SourceBadge mode={sourceMode} />
          </div>
          {decisions.length === 0 ? (
            <EmptyState
              icon="clock"
              title="No decisions recorded"
              description="Review a feasible recovery strategy to record a decision."
              action={
                <Button variant="secondary" onClick={() => navigate("/analysis")}>
                  Open analysis
                </Button>
              }
            />
          ) : (
            <div className="flex flex-col gap-6">
              {decisions.map((decision) => (
                <Button
                  className="flex w-full items-center justify-between gap-3 border-b py-4 text-left"
                  key={decision.id}
                  onClick={() =>
                    navigate(`/history/decisions/${encodeURIComponent(decision.id)}`)
                  }
                >
                  <span>
                    <strong>
                      {decision.strategyLabel || decision.message || "Recovery decision"}
                    </strong>
                    <small className="text-muted-foreground">
                      {formatDateTime(decision.approvedAt || decision.createdAt)} ·{" "}
                      {decision.targetProjectName ||
                        `Analysis ${decision.analysisId || "—"}`}
                      {decision.donorProjectName
                        ? ` · donor ${decision.donorProjectName}`
                        : ""}
                    </small>
                  </span>
                  <span>
                    {humanize(decision.status) || "Recorded"}{" "}
                    <Icon name="chevron-right" size={18} />
                  </span>
                </Button>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function DecisionDetail({
  organization,
  decisionId,
  sourceMode,
  actorId,
}: {
  organization: Organization;
  decisionId: string;
  sourceMode?: DataMode;
  actorId: string;
}) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const projectId = params.get("project");
  const projectPath = projectId
    ? `/projects/${encodeURIComponent(projectId)}`
    : "/projects";
  const isNew = decisionId === "new";
  const path = isNew
    ? null
    : scopedPath(organization.id, `/decisions/${encodeURIComponent(decisionId)}`);
  const { data, error, isLoading, mutate } = useApiResource<DecisionContract>(path);
  const calibrationPath =
    isNew || !data ? null : scopedPath(organization.id, CALIBRATION_SETUP_HOURS_RESOURCE);
  const {
    data: calibration,
    error: calibrationError,
    isLoading: isCalibrationLoading,
  } = useApiResource<CalibrationResponse>(calibrationPath);
  const [rationale, setRationale] = useState("");
  const [guardrails, setGuardrails] = useState("");
  const [includeCheckpoint, setIncludeCheckpoint] = useState(sourceMode === "fixture");
  const [checkpointDueAt, setCheckpointDueAt] = useState(defaultCheckpointDueAt);
  const [checkpointMetric, setCheckpointMetric] = useState("remaining_effort_hours");
  const [checkpointComparator, setCheckpointComparator] =
    useState<CheckpointComparator>("at_most");
  const [checkpointThreshold, setCheckpointThreshold] = useState("8");
  const [checkpointEvidenceRequirement, setCheckpointEvidenceRequirement] = useState(
    "Manager-confirmed remaining effort estimate",
  );
  const [saveError, setSaveError] = useState<string>();
  const [isSaving, setIsSaving] = useState(false);
  const analysisId = params.get("analysis") || data?.analysisId;
  const strategyId = params.get("strategy") || data?.strategyId;

  async function recordDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!analysisId || !strategyId) {
      setSaveError(
        "The contract needs a stored analysis and strategy id before it can be recorded.",
      );
      return;
    }
    const threshold = Number(checkpointThreshold);
    if (
      includeCheckpoint &&
      (!checkpointDueAt || !checkpointMetric.trim() || !Number.isFinite(threshold))
    ) {
      setSaveError(
        "Complete the checkpoint due date, metric, and numeric threshold or remove the checkpoint.",
      );
      return;
    }
    setIsSaving(true);
    setSaveError(undefined);
    try {
      const checkpoints = includeCheckpoint
        ? [
            {
              id: DEFAULT_CHECKPOINT_ID,
              dueAt: toIsoDateTime(checkpointDueAt),
              metric: checkpointMetric.trim(),
              comparator: checkpointComparator,
              threshold,
              evidenceRequirement: checkpointEvidenceRequirement.trim() || undefined,
            },
          ]
        : [];
      const saved = await request<DecisionContract>(
        scopedPath(organization.id, "/decisions"),
        jsonBody({
          analysisId,
          strategyId,
          rationale: rationale || undefined,
          guardrails: guardrails
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean),
          checkpoints,
          idempotencyKey: crypto.randomUUID(),
        }),
      );
      navigate(`/history/decisions/${encodeURIComponent(saved.id)}`, { replace: true });
    } catch (requestError) {
      setSaveError(
        getErrorMessage(requestError, "The decision contract could not be recorded."),
      );
    } finally {
      setIsSaving(false);
    }
  }

  if (isLoading)
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="decision-detail-skeleton" />
      </div>
    );
  if (error)
    return (
      <div className="flex flex-col gap-6">
        <h1>Decisions</h1>
        <ErrorPanel
          message={getErrorMessage(error)}
          onRetry={() => window.location.reload()}
        />
      </div>
    );
  if (!isNew && data)
    return (
      <RecordedDecision
        organization={organization}
        decision={data}
        sourceMode={sourceMode}
        actorId={actorId}
        calibration={calibration}
        calibrationError={calibrationError}
        isCalibrationLoading={isCalibrationLoading}
        onRefresh={() => mutate()}
        onBack={() => navigate(projectId ? projectPath : "/decisions")}
      />
    );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1>Decisions</h1>
        <Button variant="quiet" onClick={() => navigate(projectPath)}>
          Back to project
        </Button>
      </div>
      {saveError && <ErrorPanel title="Contract not recorded" message={saveError} />}
      <Card className="flex flex-col gap-6">
        <div className="flex flex-wrap items-center gap-3">
          <h2>Record decision</h2>
          <Badge tone="warning">Recorded, not applied to Timecue</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-muted-foreground">
          <span>Analysis {analysisId || "—"}</span>
          <span>Strategy {strategyId || "—"}</span>
        </div>
        <form className="flex flex-col gap-6" onSubmit={recordDecision}>
          <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
            <span>
              Manager rationale <small>optional</small>
            </span>
            <Textarea
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              placeholder="Why is this the right trade-off?"
              rows={4}
            />
          </label>
          <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
            <span>
              Guardrails <small>one per line</small>
            </span>
            <Textarea
              value={guardrails}
              onChange={(event) => setGuardrails(event.target.value)}
              placeholder="One guardrail per line"
              rows={4}
            />
          </label>
          <details>
            <summary>Checkpoint</summary>
            <div className="flex flex-col gap-6">
              <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                <span>
                  <Checkbox
                    checked={includeCheckpoint}
                    onCheckedChange={(checked) => setIncludeCheckpoint(checked === true)}
                  />{" "}
                  Add checkpoint
                </span>
              </label>
              {includeCheckpoint && (
                <div className="grid gap-6 md:grid-cols-2">
                  <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                    <span>Due at</span>
                    <Input
                      type="datetime-local"
                      value={checkpointDueAt}
                      onChange={(event) => setCheckpointDueAt(event.target.value)}
                      required
                    />
                  </label>
                  <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                    <span>Metric</span>
                    <Input
                      value={checkpointMetric}
                      onChange={(event) => setCheckpointMetric(event.target.value)}
                      required
                    />
                  </label>
                  <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                    <span>Comparator</span>
                    <SelectField
                      value={checkpointComparator}
                      onValueChange={(value) =>
                        setCheckpointComparator(value as CheckpointComparator)
                      }
                    >
                      <Choice value="at_most">At most</Choice>
                      <Choice value="at_least">At least</Choice>
                    </SelectField>
                  </label>
                  <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                    <span>Threshold</span>
                    <Input
                      type="number"
                      min="0"
                      step="0.1"
                      value={checkpointThreshold}
                      onChange={(event) => setCheckpointThreshold(event.target.value)}
                      required
                    />
                  </label>
                  <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
                    <span>
                      Evidence requirement <small>optional</small>
                    </span>
                    <Input
                      value={checkpointEvidenceRequirement}
                      onChange={(event) =>
                        setCheckpointEvidenceRequirement(event.target.value)
                      }
                    />
                  </label>
                  <span className="text-muted-foreground">
                    Definition id: {DEFAULT_CHECKPOINT_ID}
                  </span>
                </div>
              )}
            </div>
          </details>
          <Button type="submit" className="w-full" disabled={isSaving}>
            {isSaving ? "Recording contract…" : "Record decision contract"}
          </Button>
        </form>
      </Card>
    </div>
  );
}

function RecordedDecision({
  organization,
  decision,
  sourceMode,
  actorId,
  calibration,
  calibrationError,
  isCalibrationLoading,
  onRefresh,
  onBack,
}: {
  organization: Organization;
  decision: DecisionContract;
  sourceMode?: DataMode;
  actorId: string;
  calibration?: CalibrationResponse;
  calibrationError?: unknown;
  isCalibrationLoading: boolean;
  onRefresh: () => Promise<unknown>;
  onBack: () => void;
}) {
  const effectiveSourceMode = decision.sourceMode || sourceMode;
  const [implementationState, setImplementationState] =
    useState<ImplementationState>("reported");
  const [implementationNote, setImplementationNote] = useState("");
  const [observationEventAt, setObservationEventAt] = useState(currentDateTimeInput());
  const [observationKnownAt, setObservationKnownAt] = useState(currentDateTimeInput());
  const [workerId, setWorkerId] = useState(
    actionValue(decision.exactActions, "workerId"),
  );
  const [fromProjectId, setFromProjectId] = useState(
    actionValue(decision.exactActions, "fromProjectId"),
  );
  const [toProjectId, setToProjectId] = useState(
    actionValue(decision.exactActions, "toProjectId"),
  );
  const [setupHours, setSetupHours] = useState(
    actionNumber(decision.exactActions, "setupHours") || "1",
  );
  const [acceptedForCalibration, setAcceptedForCalibration] = useState(
    effectiveSourceMode === "fixture",
  );
  const [observationNote, setObservationNote] = useState("");
  const [checkpointValues, setCheckpointValues] = useState<Record<string, string>>({});
  const [checkpointEvidence, setCheckpointEvidence] = useState<Record<string, string>>(
    {},
  );
  const [pendingAction, setPendingAction] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const [actionNotice, setActionNotice] = useState<string>();
  const observations = decision.observations || [];
  const implementationEvents =
    decision.implementationEvents || observations.filter(isImplementationObservation);
  const setupObservations = observations.filter(
    (observation) => !isImplementationObservation(observation),
  );
  const checkpoints = decision.checkpoints || [];
  const evaluations = decision.checkpointEvaluations || [];
  const guardrails = guardrailItems(decision.guardrails);

  async function submitImplementation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitEvent(
      DECISION_IMPLEMENTATION_RESOURCE,
      {
        state: implementationState,
        eventAt: new Date().toISOString(),
        knownAt: new Date().toISOString(),
        note: implementationNote || undefined,
      },
      "Implementation event recorded. This is a manager report, not Timecue writeback.",
    );
    setImplementationNote("");
  }
  async function submitObservation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submitEvent(
      DECISION_OBSERVATIONS_RESOURCE,
      {
        type: "transfer_setup",
        eventAt: toIsoDateTime(observationEventAt),
        knownAt: toIsoDateTime(observationKnownAt),
        workerId: workerId.trim(),
        fromProjectId: fromProjectId.trim(),
        toProjectId: toProjectId.trim(),
        setupHours: Number(setupHours),
        acceptedForCalibration,
        note: observationNote || undefined,
      },
      acceptedForCalibration && effectiveSourceMode === "fixture"
        ? "Direct setup observation recorded and sent to fixture calibration."
        : "Direct setup observation recorded. Live calibration remains disabled.",
    );
    setObservationNote("");
  }
  async function submitCheckpoint(
    event: FormEvent<HTMLFormElement>,
    checkpointId: string,
  ) {
    event.preventDefault();
    const value = checkpointValues[checkpointId]?.trim();
    const evidenceNote = checkpointEvidence[checkpointId]?.trim();
    const hasEvidence = value !== undefined && value !== "";
    if (hasEvidence && !evidenceNote) {
      setActionError(
        "Add a manager-attested evidence note before checking a numeric checkpoint value.",
      );
      return;
    }
    await submitEvent(
      DECISION_CHECKPOINTS_RESOURCE,
      {
        checkpointId,
        asOf: new Date().toISOString(),
        observedValue: hasEvidence ? Number(value) : undefined,
        evidence: hasEvidence ? { managerNote: evidenceNote, actorId } : undefined,
      },
      "Checkpoint evaluation recorded. The frozen definition and original forecast remain unchanged.",
    );
  }
  async function submitEvent(
    resource: string,
    payload: Record<string, unknown>,
    notice: string,
  ) {
    setPendingAction(resource);
    setActionError(undefined);
    setActionNotice(undefined);
    try {
      await request(
        scopedPath(
          organization.id,
          `/decisions/${encodeURIComponent(decision.id)}/${resource}`,
        ),
        jsonBody(payload),
      );
      await onRefresh();
      setActionNotice(notice);
    } catch (requestError) {
      setActionError(
        getErrorMessage(requestError, "The closed-loop record could not be saved."),
      );
    } finally {
      setPendingAction(undefined);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1>Decisions</h1>
        <Button variant="quiet" onClick={onBack}>
          Back to history
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <h2>{decision.strategyLabel || "Recorded decision"}</h2>
        <Badge tone="warning">Recorded, not applied to Timecue</Badge>
      </div>
      {actionError && <ErrorPanel title="Record unavailable" message={actionError} />}
      {actionNotice && (
        <div className="flex flex-wrap items-center gap-3" role="status">
          {actionNotice}
        </div>
      )}
      <Card className="flex flex-col gap-6">
        <h3>Contract</h3>
        <div className="grid gap-6 md:grid-cols-2">
          <Fact label="Target" value={decision.targetProjectName || "—"} />
          <Fact label="Donor" value={decision.donorProjectName || "—"} />
          <Fact label="Status" value={humanize(decision.status) || "Approved"} />
          <Fact label="Analysis" value={decision.analysisId || "—"} />
          <Fact label="Strategy" value={decision.strategyId || "—"} />
        </div>
        {(decision.managerRationale || decision.rationale) && (
          <p>{decision.managerRationale || decision.rationale}</p>
        )}
        {guardrails.length > 0 && (
          <div className="flex flex-col gap-6">
            <strong>Guardrails</strong>
            {guardrails.map((guardrail) => (
              <span key={guardrail}>{guardrail}</span>
            ))}
          </div>
        )}
        <div className="grid gap-6 md:grid-cols-2">
          <MetricSnapshot label="Target risk" outcome={decision.target} />
          <MetricSnapshot label="Donor risk" outcome={decision.donor} />
        </div>
        <div className="flex flex-col gap-6">
          {(decision.exactActions || []).map((action, index) => (
            <span key={`${String(action.type || "action")}-${index}`}>
              {formatAction(action)}
            </span>
          ))}
        </div>
      </Card>
      <details className="rounded-xl border bg-card p-6">
        <summary>Implementation report</summary>
        <form className="flex flex-col gap-6" onSubmit={submitImplementation}>
          <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
            <span>Implementation state</span>
            <SelectField
              value={implementationState}
              onValueChange={(value) =>
                setImplementationState(value as ImplementationState)
              }
            >
              {implementationStates.map((state) => (
                <Choice key={state.value} value={state.value}>
                  {state.label}
                </Choice>
              ))}
            </SelectField>
          </label>
          <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
            <span>
              Manager note <small>optional</small>
            </span>
            <Input
              value={implementationNote}
              onChange={(event) => setImplementationNote(event.target.value)}
              placeholder="What was attempted?"
            />
          </label>
          <Button
            type="submit"
            size="sm"
            disabled={pendingAction === DECISION_IMPLEMENTATION_RESOURCE}
          >
            {pendingAction === DECISION_IMPLEMENTATION_RESOURCE
              ? "Saving…"
              : "Record implementation"}
          </Button>
        </form>
        <EventList events={implementationEvents} sourceMode={effectiveSourceMode} />
      </details>
      <details className="rounded-xl border bg-card p-6">
        <summary>Transfer setup observation</summary>
        <form className="flex flex-col gap-6" onSubmit={submitObservation}>
          <div className="grid gap-6 md:grid-cols-2">
            <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
              <span>Event at</span>
              <Input
                type="datetime-local"
                value={observationEventAt}
                onChange={(event) => setObservationEventAt(event.target.value)}
                required
              />
            </label>
            <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
              <span>Known at</span>
              <Input
                type="datetime-local"
                value={observationKnownAt}
                onChange={(event) => setObservationKnownAt(event.target.value)}
                required
              />
            </label>
            <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
              <span>Worker id</span>
              <Input
                value={workerId}
                onChange={(event) => setWorkerId(event.target.value)}
                required
              />
            </label>
            <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
              <span>Setup hours</span>
              <Input
                type="number"
                min="0"
                max="24"
                step="0.1"
                value={setupHours}
                onChange={(event) => setSetupHours(event.target.value)}
                required
              />
            </label>
            <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
              <span>From project id</span>
              <Input
                value={fromProjectId}
                onChange={(event) => setFromProjectId(event.target.value)}
                required
              />
            </label>
            <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
              <span>To project id</span>
              <Input
                value={toProjectId}
                onChange={(event) => setToProjectId(event.target.value)}
                required
              />
            </label>
          </div>
          <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
            <span>
              Observation note <small>optional</small>
            </span>
            <Textarea
              value={observationNote}
              onChange={(event) => setObservationNote(event.target.value)}
              placeholder="What was observed?"
              rows={3}
            />
          </label>
          <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
            <span>
              <Checkbox
                checked={acceptedForCalibration}
                onCheckedChange={(checked) => setAcceptedForCalibration(checked === true)}
              />{" "}
              Eligible for calibration review
            </span>
          </label>
          <Button
            type="submit"
            disabled={pendingAction === DECISION_OBSERVATIONS_RESOURCE}
          >
            {pendingAction === DECISION_OBSERVATIONS_RESOURCE
              ? "Saving…"
              : "Record observation"}
          </Button>
        </form>
        <ObservationList
          observations={setupObservations}
          sourceMode={effectiveSourceMode}
        />
      </details>
      <details className="rounded-xl border bg-card p-6">
        <summary>Checkpoints and calibration</summary>
        {checkpoints.length === 0 ? (
          <EmptyState
            icon="clock"
            title="No checkpoint"
            description="No checkpoint was recorded."
          />
        ) : (
          <div className="flex flex-col gap-6">
            {checkpoints.map((checkpoint) => (
              <CheckpointCard
                key={checkpoint.id}
                checkpoint={checkpoint}
                evaluation={latestEvaluation(evaluations, checkpoint.id)}
                value={checkpointValues[checkpoint.id || ""] || ""}
                evidence={checkpointEvidence[checkpoint.id || ""] || ""}
                isPending={pendingAction === DECISION_CHECKPOINTS_RESOURCE}
                onValueChange={(value) =>
                  setCheckpointValues((current) => ({
                    ...current,
                    [checkpoint.id || ""]: value,
                  }))
                }
                onEvidenceChange={(value) =>
                  setCheckpointEvidence((current) => ({
                    ...current,
                    [checkpoint.id || ""]: value,
                  }))
                }
                onSubmit={(event) => submitCheckpoint(event, checkpoint.id || "")}
              />
            ))}
          </div>
        )}
        <CalibrationCard
          calibration={calibration}
          error={calibrationError}
          isLoading={isCalibrationLoading}
          sourceMode={effectiveSourceMode}
        />
      </details>
    </div>
  );
}

function CheckpointCard({
  checkpoint,
  evaluation,
  value,
  evidence,
  isPending,
  onValueChange,
  onEvidenceChange,
  onSubmit,
}: {
  checkpoint: NonNullable<DecisionContract["checkpoints"]>[number];
  evaluation?: CheckpointEvaluation;
  value: string;
  evidence: string;
  isPending: boolean;
  onValueChange: (value: string) => void;
  onEvidenceChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const state = evaluation?.state as CheckpointState | undefined;
  return (
    <div className="mt-5 space-y-4 border-t pt-5">
      <div className="flex flex-wrap justify-between gap-4">
        <div>
          <strong>{checkpoint.metric || "Checkpoint"}</strong>
          <span>
            Due {formatDateTime(checkpoint.dueAt)} ·{" "}
            {checkpoint.comparator === "at_least" ? "at least" : "at most"}{" "}
            {checkpoint.threshold ?? "—"}
          </span>
        </div>
        <Badge tone={checkpointTone(state)}>{humanize(state) || "Not checked"}</Badge>
      </div>
      {checkpoint.evidenceRequirement && (
        <p className="text-xs text-muted-foreground">
          <Icon name="file-text" size={14} /> {checkpoint.evidenceRequirement}
        </p>
      )}
      {evaluation && (
        <div className="my-3 text-xs text-muted-foreground">
          <span>
            Last evaluation {formatDateTime(evaluation.evaluatedAt || evaluation.asOf)}
          </span>
          <strong>
            {typeof evaluation.observedValue === "number"
              ? evaluation.observedValue
              : "No value supplied"}
          </strong>
        </div>
      )}
      <form className="mt-5 flex flex-col gap-4" onSubmit={onSubmit}>
        <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
          <span>
            Observed value <small>leave blank for needs data</small>
          </span>
          <Input
            type="number"
            step="0.1"
            value={value}
            onChange={(event) => onValueChange(event.target.value)}
          />
        </label>
        <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
          <span>
            Evidence note <small>optional when a value is supplied</small>
          </span>
          <Input
            value={evidence}
            onChange={(event) => onEvidenceChange(event.target.value)}
            placeholder="Manager-confirmed evidence"
          />
        </label>
        <Button type="submit" size="sm" icon="check" disabled={isPending}>
          {isPending ? "Checking…" : "Check checkpoint"}
        </Button>
      </form>
    </div>
  );
}

function CalibrationCard({
  calibration,
  error,
  isLoading,
  sourceMode,
}: {
  calibration?: CalibrationResponse;
  error?: unknown;
  isLoading: boolean;
  sourceMode?: DataMode;
}) {
  const current = calibrationVersion(calibration);
  return (
    <Card className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h3>Setup-hours calibration</h3>
        <SourceBadge mode={calibration?.sourceMode || sourceMode} />
      </div>
      {isLoading && <Skeleton className="calibration-skeleton" />}
      {Boolean(error) && (
        <ErrorPanel
          title="Calibration unavailable"
          message={getErrorMessage(error, "Calibration could not be loaded.")}
        />
      )}
      {!isLoading && !error && (
        <>
          {current ? (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <span>Current mean</span>
                <strong>
                  {formatHours(current.meanHours ?? current.updatedMeanHours)}
                </strong>
                <Badge tone={current.status === "calibrated" ? "success" : "warning"}>
                  {humanize(current.status) || "Assumed"}
                </Badge>
              </div>
              <div className="grid gap-6 md:grid-cols-2">
                <Fact label="Version" value={stringValue(current.version)} />
                <Fact label="Prior strength" value={stringValue(current.priorStrength)} />
                <Fact
                  label="Included samples"
                  value={stringValue(
                    current.totalSampleCount ?? current.previousSampleCount,
                  )}
                />
                <Fact
                  label="Updated"
                  value={formatDateTime(current.updatedAt || current.createdAt)}
                />
              </div>
              {calibration?.history && calibration.history.length > 0 && (
                <div className="flex flex-col gap-6">
                  <span>Version history</span>
                  {calibration.history
                    .slice(-3)
                    .reverse()
                    .map((version) => (
                      <div
                        className="flex flex-wrap items-center gap-3"
                        key={`${version.version}-${version.createdAt}`}
                      >
                        <strong>v{version.version ?? "—"}</strong>
                        <span>
                          {formatHours(version.meanHours ?? version.updatedMeanHours)} ·{" "}
                          {stringValue(
                            version.totalSampleCount ?? version.previousSampleCount,
                          )}{" "}
                          samples
                        </span>
                      </div>
                    ))}
                </div>
              )}
            </>
          ) : (
            <EmptyState
              icon="clock"
              title="No calibration yet"
              description="No eligible setup observation has updated calibration."
            />
          )}
        </>
      )}
    </Card>
  );
}

function EventList({
  events,
  sourceMode,
}: {
  events: DecisionImplementationEvent[];
  sourceMode?: DataMode;
}) {
  return events.length === 0 ? (
    <div className="py-4 text-muted-foreground">
      <Icon name="clock" size={15} />
      <span>No implementation event has been recorded.</span>
    </div>
  ) : (
    <div className="mt-5">
      {events
        .slice()
        .reverse()
        .map((event) => (
          <div className="space-y-2 border-t py-4" key={event.id}>
            <div className="hidden">
              <Icon name="check" size={14} />
            </div>
            <div>
              <div className="flex items-center gap-3">
                <strong>{humanize(event.state) || "Implementation report"}</strong>
                <SourceBadge mode={event.sourceMode || sourceMode} />
              </div>
              <p>
                {event.note || "Manager reported an implementation state without a note."}
              </p>
              <span>
                {formatDateTime(event.eventAt || event.createdAt)} · known{" "}
                {formatDateTime(event.knownAt)}
              </span>
            </div>
          </div>
        ))}
    </div>
  );
}
function ObservationList({
  observations,
  sourceMode,
}: {
  observations: DecisionObservation[];
  sourceMode?: DataMode;
}) {
  return observations.length === 0 ? (
    <div className="py-4 text-muted-foreground">
      <Icon name="file-text" size={15} />
      <span>No direct setup observation has been recorded.</span>
    </div>
  ) : (
    <div className="mt-5">
      {observations
        .slice()
        .reverse()
        .map((observation) => (
          <div className="space-y-2 border-t py-4" key={observation.id}>
            <div className="hidden">
              <Icon name="file-text" size={14} />
            </div>
            <div>
              <div className="flex items-center gap-3">
                <strong>
                  {formatHours(observation.setupHours)} setup ·{" "}
                  {observation.workerId || "worker not named"}
                </strong>
                <SourceBadge mode={observation.sourceMode || sourceMode} />
              </div>
              <p>
                {observation.fromProjectId || "—"} → {observation.toProjectId || "—"}
                {observation.note ? ` · ${observation.note}` : ""}
              </p>
              <span>
                {formatDateTime(observation.eventAt)} · known{" "}
                {formatDateTime(observation.knownAt)}
                {observation.acceptedForCalibration ? " · calibration accepted" : ""}
              </span>
            </div>
          </div>
        ))}
    </div>
  );
}

function latestEvaluation(
  evaluations: CheckpointEvaluation[],
  checkpointId?: string,
): CheckpointEvaluation | undefined {
  if (!checkpointId) return undefined;
  return evaluations
    .slice()
    .reverse()
    .find((evaluation) => evaluation.checkpointId === checkpointId);
}
function checkpointTone(
  state?: CheckpointState,
): "neutral" | "success" | "warning" | "danger" {
  if (state === "met") return "success";
  if (state === "breached") return "danger";
  if (state === "needs_data") return "warning";
  return "neutral";
}
function isImplementationObservation(observation: DecisionObservation): boolean {
  return (observation.observationType || observation.type) === "implementation";
}
function calibrationVersion(
  response?: CalibrationResponse,
): CalibrationVersion | undefined {
  if (!response) return undefined;
  return (
    response.current ||
    (response.version !== undefined ||
    response.meanHours !== undefined ||
    response.updatedMeanHours !== undefined
      ? response
      : undefined)
  );
}
function guardrailItems(value: DecisionContract["guardrails"]): string[] {
  if (Array.isArray(value)) return value;
  if (!value) return [];
  return Object.entries(value).map(([key, item]) => `${humanize(key)}: ${String(item)}`);
}
function actionValue(actions: DecisionContract["exactActions"], key: string): string {
  const action = actions?.find((item) => item.type === "transfer");
  const value = action?.[key];
  return typeof value === "string" ? value : "";
}
function actionNumber(actions: DecisionContract["exactActions"], key: string): string {
  const action = actions?.find((item) => item.type === "transfer");
  const value = action?.[key];
  return typeof value === "number" ? String(Number(value.toFixed(1))) : "";
}
function formatAction(action: Record<string, unknown>): string {
  if (action.type === "transfer")
    return `Transfer ${String(action.workerId || "worker")} · ${String(action.fromProjectId || "donor")} → ${String(action.toProjectId || "target")}`;
  return humanize(typeof action.type === "string" ? action.type : "Approved action");
}
function stringValue(value?: string | number): string {
  return value === undefined || value === null ? "—" : String(value);
}
function defaultCheckpointDueAt(): string {
  const date = new Date();
  date.setDate(date.getDate() + 2);
  return currentDateTimeInput(date);
}
function currentDateTimeInput(date = new Date()): string {
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16);
}
function toIsoDateTime(value: string): string {
  return new Date(value).toISOString();
}
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 [&_span]:text-xs [&_span]:text-muted-foreground">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function LineageFact({ label, value }: { label: string; value?: string | number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{stringValue(value)}</strong>
    </div>
  );
}
function MetricSnapshot({
  label,
  outcome,
}: {
  label: string;
  outcome?: { delayProbability?: number; expectedPositiveDelayDays?: number };
}) {
  return (
    <div className="space-y-2 py-3 [&_span]:block [&_span]:text-xs [&_strong]:text-xl">
      <span>{label}</span>
      <strong>{formatPercent(outcome?.delayProbability)}</strong>
      <small>{formatDays(outcome?.expectedPositiveDelayDays)} expected delay</small>
    </div>
  );
}
