import { SelectField, Choice } from "../components/SelectField";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorPanel, Skeleton } from "../components/ui";
import { getErrorMessage, jsonBody, request, scopedPath } from "../lib/api";
import {
  formatDateTime,
  formatDays,
  formatHours,
  formatPercent,
  humanize,
  outcomeForProject,
} from "../lib/format";
import { useApiResource, useOnlineStatus } from "../lib/hooks";
import { AnalysisChart } from "../components/AnalysisChart";
import { BackToWorkspace } from "../components/BackToWorkspace";
import type {
  AnalysisReadinessResponse,
  AnalysisResponse,
  Organization,
  Outcome,
  PortfolioResponse,
  ReadinessIssue,
  Strategy,
} from "../lib/types";

export function AnalysisPage({
  organization,
  projectId,
  initialAnalysisId,
  onCompleted,
}: {
  organization: Organization;
  projectId?: string;
  initialAnalysisId?: string;
  onCompleted?: () => void;
}) {
  const navigate = useNavigate();
  const online = useOnlineStatus();
  const [params] = useSearchParams();
  const portfolio = useApiResource<PortfolioResponse>(
    scopedPath(organization.id, "/portfolio"),
  );
  const projects = portfolio.data?.projects || [];
  const [selectedProjectId, setSelectedProjectId] = useState(
    projectId || params.get("project") || "",
  );
  const [analysisId, setAnalysisId] = useState<string | undefined>(
    initialAnalysisId || params.get("analysis") || undefined,
  );
  const [runState, setRunState] = useState<"idle" | "submitting" | "error">("idle");
  const [runError, setRunError] = useState<string>();
  const [readinessIssues, setReadinessIssues] = useState<ReadinessIssue[]>([]);
  const analysisPath = analysisId
    ? scopedPath(organization.id, `/analyses/${encodeURIComponent(analysisId)}`)
    : null;
  const analysis = useApiResource<AnalysisResponse>(analysisPath, 1800);
  const selectedProject =
    projects.find((project) => project.id === selectedProjectId) || projects[0];
  const completeAnalysis =
    analysis.data?.status === "complete" || analysis.data?.status === "completed"
      ? analysis.data
      : undefined;
  const baseline = completeAnalysis?.baselineByProject?.[selectedProject?.id || ""];
  const strategies = useMemo(
    () =>
      (completeAnalysis?.strategies || []).filter(
        (strategy) => strategy.id !== "baseline" && strategy.feasible !== false,
      ),
    [completeAnalysis?.strategies],
  );

  async function runAnalysis(mode: "baseline" | "recovery") {
    if (!selectedProject?.id) return;
    setRunState("submitting");
    setRunError(undefined);
    try {
      const response = await request<
        { id: string } | AnalysisResponse | AnalysisReadinessResponse
      >(
        scopedPath(organization.id, "/analyses"),
        jsonBody({ projectId: selectedProject.id, mode }),
      );
      if ("status" in response && response.status === "needs_inputs") {
        setReadinessIssues(response.readinessIssues || []);
        setAnalysisId(undefined);
        setRunState("idle");
        return;
      }
      const nextId = "id" in response ? response.id : undefined;
      if (!nextId) throw new Error("The analysis request did not return a job id.");
      setAnalysisId(nextId);
      onCompleted?.();
      setReadinessIssues([]);
      setRunState("idle");
    } catch (requestError) {
      setRunState("error");
      setRunError(getErrorMessage(requestError, "The analysis could not be started."));
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        {!projectId && <BackToWorkspace />}
        <h2>Analysis</h2>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => runAnalysis("recovery")}
            disabled={!online || !selectedProject?.id || runState === "submitting"}
          >
            {runState === "submitting" ? "Analyzing…" : "Run analysis"}
          </Button>
        </div>
      </div>
      {runError && (
        <ErrorPanel
          title="Analysis request failed"
          message={runError}
          onRetry={() => runAnalysis("baseline")}
        />
      )}
      {readinessIssues.length > 0 && <ReadinessRequired issues={readinessIssues} />}
      {portfolio.error && (
        <ErrorPanel
          title="Project context unavailable"
          message={getErrorMessage(portfolio.error, "Projects could not be loaded.")}
          onRetry={() => portfolio.mutate()}
        />
      )}
      {portfolio.isLoading && <Skeleton className="analysis-control-skeleton" />}
      {!projectId && projects.length > 0 && (
        <label className="flex flex-col gap-2 rounded-xl border bg-card p-6">
          <span>Target project</span>
          <SelectField
            value={selectedProject?.id || ""}
            onValueChange={(value) => {
              setSelectedProjectId(value);
              setAnalysisId(undefined);
            }}
          >
            <Choice value="" disabled>
              Select a project
            </Choice>
            {projects.map((project) => (
              <Choice key={project.id} value={project.id}>
                {project.name}
              </Choice>
            ))}
          </SelectField>
          <small className="text-muted-foreground">
            Target{" "}
            {selectedProject?.targetFinishAt
              ? formatDateTime(selectedProject.targetFinishAt)
              : "—"}
          </small>
        </label>
      )}
      {analysis.error && (
        <ErrorPanel
          title="Analysis unavailable"
          message={getErrorMessage(
            analysis.error,
            "The stored analysis could not be read.",
          )}
          onRetry={() => analysis.mutate()}
        />
      )}
      {(analysis.data?.status === "running" || analysis.data?.status === "queued") && (
        <AnalysisPending status={analysis.data.status} />
      )}
      {analysis.data?.status === "failed" && (
        <ErrorPanel
          title="Analysis did not finish"
          message={analysis.data.error || "The analysis failed."}
        />
      )}
      {!analysis.data && !portfolio.isLoading && projects.length === 0 && (
        <EmptyState
          icon="layers"
          title="No target project"
          description="No project was returned by the portfolio read."
        />
      )}
      {!analysis.data && !portfolio.isLoading && projects.length > 0 && (
        <EmptyState
          icon="spark"
          title="No analysis run"
          description="Run an analysis to compare your current plan with recovery options."
        />
      )}
      {completeAnalysis && (
        <AnalysisResults
          analysis={completeAnalysis}
          selectedProjectId={selectedProject?.id || completeAnalysis.projectId}
          baseline={baseline}
          strategies={strategies}
          onRunRecovery={() => runAnalysis("recovery")}
          onRecord={(strategy) =>
            navigate(
              `/history/decisions/new?analysis=${encodeURIComponent(completeAnalysis.id)}&strategy=${encodeURIComponent(strategy.id)}&project=${encodeURIComponent(selectedProject?.id || completeAnalysis.projectId)}`,
            )
          }
        />
      )}
    </div>
  );
}

function ReadinessRequired({ issues }: { issues: ReadinessIssue[] }) {
  return (
    <Card className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h2>Needs planning inputs</h2>
        <Badge tone="warning">Review</Badge>
      </div>
      <div className="flex flex-col gap-6">
        {issues.slice(0, 4).map((issue, index) => (
          <span key={issue.id || `${issue.code}-${index}`}>
            {issue.title || issue.code || "Planning input"}
            {issue.detail ? ` · ${issue.detail}` : ""}
          </span>
        ))}
      </div>
    </Card>
  );
}

function AnalysisPending({ status }: { status: "queued" | "running" }) {
  return (
    <Card className="flex flex-wrap items-center gap-3">
      <Badge tone="warning">{status === "queued" ? "Queued" : "Running"}</Badge>
      <span>Waiting for the stored analysis result.</span>
    </Card>
  );
}

function AnalysisResults({
  analysis,
  selectedProjectId,
  baseline,
  strategies,
  onRunRecovery,
  onRecord,
}: {
  analysis: AnalysisResponse;
  selectedProjectId: string;
  baseline?: Outcome;
  strategies: Strategy[];
  onRunRecovery: () => void;
  onRecord: (strategy: Strategy) => void;
}) {
  return (
    <div className="flex flex-col gap-6">
      <AnalysisChart analysis={analysis} />
      <details className="rounded-xl border bg-card p-6">
        <summary>Analysis details</summary>
        <div className="flex flex-wrap items-center gap-3 text-muted-foreground">
          <span>Id {analysis.id}</span>
          <span>Mode {humanize(analysis.mode)}</span>
          <span>Revision {analysis.snapshotRevision || "—"}</span>
          <span>
            Seed {analysis.seed ?? "—"} · {analysis.sampleCount ?? "—"} samples
          </span>
        </div>
        <div className="flex flex-col gap-6">
          <h3>Diagnostics</h3>
          {(analysis.diagnostics || []).length === 0 ? (
            <p className="text-muted-foreground">No diagnostics returned.</p>
          ) : (
            analysis.diagnostics?.map((diagnostic, index) => (
              <div
                className="flex flex-wrap items-center gap-3"
                key={diagnostic.id || `${diagnostic.kind}-${index}`}
              >
                <Badge
                  tone={
                    diagnostic.severity === "blocking"
                      ? "danger"
                      : diagnostic.severity === "warning"
                        ? "warning"
                        : "neutral"
                  }
                >
                  {humanize(diagnostic.kind)}
                </Badge>
                <span>
                  <strong>{diagnostic.label || "Diagnostic"}</strong>
                  <small className="text-muted-foreground">
                    {diagnostic.detail || "No further detail."}
                  </small>
                </span>
              </div>
            ))
          )}
        </div>
      </details>
      <Card>
        <h2>Baseline</h2>
        <div className="grid gap-6 md:grid-cols-2">
          <OutcomeRow
            label="Delay risk"
            value={formatPercent(baseline?.delayProbability)}
          />
          <OutcomeRow
            label="Expected positive delay"
            value={formatDays(baseline?.expectedPositiveDelayDays)}
          />
          <OutcomeRow
            label="Target finish p50"
            value={baseline?.finishP50 ? formatDateTime(baseline.finishP50) : "—"}
          />
          <OutcomeRow label="Unfinished runs" value={baseline?.unfinishedCount ?? "—"} />
        </div>
      </Card>
      <div className="flex flex-wrap items-center gap-3">
        <h2>Recovery options</h2>
        <Button variant="secondary" onClick={onRunRecovery}>
          Generate recovery options
        </Button>
      </div>
      {strategies.length === 0 ? (
        <Card>
          <EmptyState
            icon="users"
            title="No feasible recovery returned"
            description="No feasible strategy was returned."
          />
        </Card>
      ) : (
        <div className="grid gap-6 md:grid-cols-2">
          {strategies.slice(0, 3).map((strategy) => (
            <StrategyCard
              key={strategy.id}
              strategy={strategy}
              selected={strategy.labels?.includes("recommended") === true}
              onRecord={() => onRecord(strategy)}
              selectedProjectId={selectedProjectId}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function OutcomeRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-muted-foreground">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StrategyCard({
  strategy,
  selected,
  onRecord,
  selectedProjectId,
}: {
  strategy: Strategy;
  selected: boolean;
  onRecord: () => void;
  selectedProjectId: string;
}) {
  const target =
    strategy.target || outcomeForProject(strategy.outcomesByProject, selectedProjectId);
  const donorProjectId = strategy.affectedProjectIds?.find(
    (projectId) => projectId !== selectedProjectId,
  );
  const donor =
    strategy.donor ||
    (donorProjectId
      ? outcomeForProject(strategy.outcomesByProject, donorProjectId)
      : undefined);
  const exactChanges =
    strategy.exactChanges ||
    strategy.actions
      ?.map((action) =>
        humanize(typeof action.type === "string" ? action.type : undefined),
      )
      .filter((change) => change !== "—");
  return (
    <Card className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <h3>{humanize(strategy.label || strategy.name || "Recovery strategy")}</h3>
        <Badge
          tone={strategy.feasible === false ? "danger" : selected ? "success" : "neutral"}
        >
          {strategy.feasible === false
            ? "Not feasible"
            : selected
              ? "Recommended"
              : "Feasible"}
        </Badge>
      </div>
      {(strategy.summary || strategy.rationale) && (
        <p>{strategy.summary || strategy.rationale}</p>
      )}
      <div className="grid gap-6 md:grid-cols-2">
        <OutcomeRow
          label="Target project"
          value={`${formatPercent(target?.delayProbability)} · ${formatDays(target?.expectedPositiveDelayDays)}`}
        />
        <OutcomeRow
          label="Donor impact"
          value={`${formatPercent(donor?.delayProbability)} · ${formatDays(donor?.expectedPositiveDelayDays)}`}
        />
      </div>
      {strategy.transfer && (
        <div className="flex flex-col gap-6">
          <strong>
            {strategy.transfer.workerName ||
              strategy.transfer.workerId ||
              "Existing worker"}
          </strong>
          <span className="text-muted-foreground">
            {strategy.transfer.fromProjectName ||
              strategy.transfer.fromProjectId ||
              "Donor"}{" "}
            →{" "}
            {strategy.transfer.toProjectName || strategy.transfer.toProjectId || "Target"}
          </span>
          <span className="text-muted-foreground">
            Window{" "}
            {strategy.transfer.windowStartAt
              ? formatDateTime(strategy.transfer.windowStartAt)
              : "—"}{" "}
            →{" "}
            {strategy.transfer.windowEndAt
              ? formatDateTime(strategy.transfer.windowEndAt)
              : "—"}{" "}
            · setup {formatHours(strategy.transfer.setupHours)} · travel{" "}
            {formatHours(strategy.transfer.travelHours)} · donor buffer{" "}
            {formatDays(strategy.transfer.donorBufferDays)}
          </span>
        </div>
      )}
      {exactChanges && exactChanges.length > 0 && (
        <div className="flex flex-col gap-6">
          {exactChanges.map((change) => (
            <span key={change}>{change}</span>
          ))}
        </div>
      )}
      <Button className="w-full" onClick={onRecord}>
        Review decision contract
      </Button>
    </Card>
  );
}
