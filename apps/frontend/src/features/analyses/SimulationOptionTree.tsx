import { useMemo, useRef, useState } from "react";
import { LiveSimulationTree } from "../../components/LiveSimulationTree";
import { ActionParameterList } from "@/components/ActionParameterList";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Button as LinkButton } from "@/components/ui/button";
import { formatDate, formatPercent } from "@/lib/format";
import type { AnalysisResponse, Strategy } from "@/lib/types";

type RecordValue = Record<string, unknown>;
type EvaluatedAnalysis = AnalysisResponse & { candidateEvaluations?: unknown };
type Option = {
  id: string;
  title: string;
  actions: string[];
  parameters: RecordValue[];
  feasible?: boolean;
  reasons: string[];
  outcomes: RecordValue;
  deltas: RecordValue;
  published?: Strategy;
};
const BASELINE = "baseline";

function uncertaintyRange(outcome: RecordValue): string {
  const low = text(outcome.finishP10) || text(outcome.p10);
  const high = text(outcome.finishP90) || text(outcome.p90);
  return `${low ? formatDate(low) : "Unknown"} – ${high ? formatDate(high) : "Unknown"} (10th–90th percentile)`;
}

function sampleSummary(outcome: RecordValue): string {
  const samples = finite(outcome.sampleCount);
  const unfinished = finite(outcome.unfinishedCount);
  const unknown = finite(outcome.unknownCensoredCount);
  return [
    samples === undefined ? "Sample count not reported" : `${samples} samples`,
    unfinished === undefined
      ? "Unfinished count not reported"
      : `${unfinished} unfinished`,
    unknown === undefined ? undefined : `${unknown} with uncertain lateness`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function record(value: unknown): RecordValue {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as RecordValue)
    : {};
}

function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  if (/[0-9a-f]{8}-[0-9a-f-]{27,}/i.test(value)) return undefined;
  return value;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.flatMap((item) => (text(item) ? [text(item)!] : []))
    : [];
}

/** Read labels from the frozen enriched actions; no action is inferred from a role. */
function actionLabel(value: unknown): string {
  const action = record(value);
  const supplied = text(action.summary) || text(action.description);
  if (supplied) return supplied;
  switch (action.type) {
    case "transfer":
      return `Move ${text(action.workerName) || "a worker"} to ${text(action.toProjectName) || "the receiving project"}`;
    case "overtime":
      return `Add overtime for ${text(action.workerName) || "the assigned worker"}`;
    case "priority":
    case "resequence":
      return "Change the order of planned work";
    case "date_shift":
      return `Change the planned date for ${text(action.taskName) || "the task"}`;
    case "material":
      return `Review material readiness for ${text(action.taskName) || "the task"}`;
    case "weather":
      return "Review the weather work window";
    default:
      return "Review the supplied planning change";
  }
}

function outcomeMap(value: unknown): RecordValue {
  if (!Array.isArray(value)) return record(value);
  return Object.fromEntries(
    value.flatMap((item) => {
      const outcome = record(item);
      return typeof outcome.projectId === "string" ? [[outcome.projectId, outcome]] : [];
    }),
  );
}

/** Candidate IDs are matched to published strategies, never promoted by the browser. */
function evaluatedOptions(analysis: EvaluatedAnalysis, published: Strategy[]): Option[] {
  const candidates = Array.isArray(analysis.candidateEvaluations)
    ? analysis.candidateEvaluations
    : [];
  const byId = new Map<string, RecordValue>();
  for (const item of [...candidates, ...published]) {
    const candidate = record(item);
    if (typeof candidate.id !== "string" || candidate.id === BASELINE) continue;
    byId.set(candidate.id, { ...byId.get(candidate.id), ...candidate });
  }
  const publishedById = new Map(published.map((strategy) => [strategy.id, strategy]));
  return [...byId.values()].map((candidate) => {
    const id = candidate.id as string;
    const actions = Array.isArray(candidate.actions)
      ? candidate.actions.map(actionLabel)
      : strings(candidate.exactChanges);
    const summary = text(candidate.summary);
    return {
      id,
      title:
        summary &&
        !/^(fast|balanced|safe|keep the current plan)\.?$/i.test(summary.trim())
          ? summary
          : actions.join(" + ") || "Review evaluated option",
      actions,
      parameters: Array.isArray(candidate.actions) ? candidate.actions.map(record) : [],
      feasible: typeof candidate.feasible === "boolean" ? candidate.feasible : undefined,
      reasons: Array.isArray(candidate.rejectionReasons)
        ? candidate.rejectionReasons.filter(
            (reason): reason is string => typeof reason === "string",
          )
        : [],
      outcomes: outcomeMap(candidate.outcomesByProject),
      deltas: record(candidate.deltasByProject),
      published: publishedById.get(id),
    };
  });
}

function reasonLabel(reason: string): string {
  // Engine reasons may suffix source IDs. Only their human-readable category is shown.
  return reason
    .split(":")[0]
    .replace(/_/g, " ")
    .replace(/^./, (letter) => letter.toUpperCase());
}

function OptionGraphCanvas({
  options,
  onSelect,
  analysis,
}: {
  options: Option[];
  selectedId: string;
  onSelect: (id: string) => void;
  analysis: AnalysisResponse;
}) {
  const entries = [
    {
      id: BASELINE,
      title: "Current plan",
      parameters: [],
      outcomes: analysis.baselineByProject || {},
      feasible: true,
    },
    ...options,
  ];
  const nodes = entries.map((option) => {
    const outcomes = Object.entries(option.outcomes).map(([projectId, value]) => {
      const outcome = record(value);
      return {
        projectId,
        projectName: text(outcome.projectName) || "Project",
        delayProbability: finite(outcome.delayProbability),
        finishP50: text(outcome.finishP50),
        unfinishedCount: finite(outcome.unfinishedCount),
      };
    });
    const first = record(Object.values(option.outcomes)[0]);
    const samples = finite(first.sampleCount) || 0;
    return {
      id: option.id,
      label: option.title,
      status: option.feasible === false ? ("rejected" as const) : ("completed" as const),
      completedSamples: samples,
      totalSamples: samples,
      actions: option.parameters,
      feasible: option.feasible,
      outcomes,
    };
  });
  return (
    <LiveSimulationTree
      saved
      onSelectNode={onSelect}
      job={{
        id: analysis.id,
        status: "completed",
        simulationProgress: {
          nodes,
          samplesPerCandidate: nodes[0]?.totalSamples || 0,
          totalCandidates: options.length,
          completedCandidates: options.length,
        },
      }}
    />
  );
}

export function SimulationOptionTree({
  analysis,
  strategies,
  onPublishedSelect,
}: {
  analysis: EvaluatedAnalysis;
  strategies: Strategy[];
  onPublishedSelect: (id: string) => void;
}) {
  const options = useMemo(
    () => evaluatedOptions(analysis, strategies),
    [analysis, strategies],
  );
  const [selectedId, setSelectedId] = useState<string>();
  const [showOthers, setShowOthers] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const returnFocus = useRef<HTMLElement | SVGElement | null>(null);
  const promoted = options
    .filter((option) => option.published)
    .sort(
      (left, right) =>
        strategies.findIndex((strategy) => strategy.id === left.id) -
        strategies.findIndex((strategy) => strategy.id === right.id),
    );
  const others = options.filter((option) => !option.published);
  const preferredId = analysis.recommendation?.strategyId || analysis.selectedScenarioId;
  const selected = options.find((option) => option.id === (selectedId || preferredId));
  const baselineSelected = selectedId === BASELINE || !selected;
  const outcomes = baselineSelected
    ? outcomeMap(analysis.baselineByProject)
    : selected.outcomes;
  const baseline = outcomeMap(analysis.baselineByProject);
  const projectIds = [...new Set([...Object.keys(baseline), ...Object.keys(outcomes)])];
  const published = baselineSelected
    ? strategies.find((strategy) => strategy.id === BASELINE)
    : selected.published;
  const canReview = Boolean(
    published &&
    !analysis.stale &&
    published.feasible !== false &&
    published.permitsApproval !== false &&
    !published.approvalBlockers?.length &&
    (baselineSelected || (selected.feasible === true && selected.reasons.length === 0)),
  );
  const choose = (id: string) => {
    const active = document.activeElement;
    if (active instanceof HTMLElement || active instanceof SVGElement)
      returnFocus.current = active;
    setSelectedId(id);
    setSheetOpen(true);
    if (strategies.some((strategy) => strategy.id === id)) onPublishedSelect(id);
  };

  return (
    <div className="space-y-5">
      <section
        className="rounded-xl border border-border bg-card p-5 sm:p-6"
        aria-label="Evaluated decision tree"
      >
        <p className="mb-4 text-sm leading-6 text-muted-foreground">
          Each node is an evaluated option. The tree groups alternatives for browsing; it
          does not mean one option depends on another.
        </p>
        <OptionGraphCanvas
          analysis={analysis}
          options={[...promoted, ...(showOthers ? others : [])]}
          selectedId={baselineSelected ? BASELINE : selected.id}
          onSelect={choose}
        />
        {others.length > 0 && (
          <Button
            variant="quiet"
            className="mt-3"
            aria-expanded={showOthers}
            onClick={() => setShowOthers((value) => !value)}
          >
            {showOthers
              ? "Hide other evaluated options"
              : `Other evaluated options (${others.length})`}
          </Button>
        )}
        {options.length === 0 && (
          <p className="mt-4 text-sm text-muted-foreground">
            This saved run contains no alternative evaluations. The current plan may still
            be late or uncertain.
          </p>
        )}
      </section>

      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent
          className="overflow-y-auto p-5 sm:max-w-2xl sm:p-7"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            returnFocus.current?.focus();
          }}
        >
          <SheetHeader className="pr-10">
            <SheetTitle>{baselineSelected ? "Current plan" : selected.title}</SheetTitle>
            <SheetDescription>
              Saved simulation inputs and results. All changes in this option were
              evaluated together.
            </SheetDescription>
          </SheetHeader>
          <section className="space-y-4 border-t border-border pt-5">
            <h2 className="text-xl font-medium">
              {baselineSelected ? "No proposed changes" : "Parameters tested"}
            </h2>
            {!baselineSelected && selected.parameters.length === 0 && (
              <ul className="space-y-2 text-sm text-muted-foreground">
                {selected.actions.map((action, index) => (
                  <li key={index}>{action}</li>
                ))}
              </ul>
            )}
            {!baselineSelected && selected.parameters.length > 0 && (
              <ActionParameterList
                actions={selected.parameters}
                references={analysis.projects}
              />
            )}
            {!baselineSelected && selected.parameters.length === 0 && (
              <p className="text-sm text-muted-foreground">
                Exact action parameters were not included in this saved result.
              </p>
            )}
            {!baselineSelected && selected.reasons.length > 0 && (
              <ul className="space-y-2 text-sm text-destructive">
                {selected.reasons.map((reason, index) => (
                  <li key={index}>{reasonLabel(reason)}</li>
                ))}
              </ul>
            )}
            {!baselineSelected && selected.feasible === undefined && (
              <p className="text-sm text-muted-foreground">
                Feasibility was not reported.
              </p>
            )}
            {!published && (
              <p className="text-sm text-muted-foreground">
                This evaluated branch is read-only. It was not published for approval.
              </p>
            )}
            {analysis.stale && (
              <p className="text-sm text-destructive">
                Run a fresh analysis before approving changes.
              </p>
            )}
            {published?.permitsApproval === false && (
              <p className="text-sm text-destructive">
                This option is not available for approval with the current inputs.
              </p>
            )}
            {canReview && (
              <LinkButton asChild className="mt-auto min-h-11 self-start">
                <Link
                  to={`/decisions/${encodeURIComponent(analysis.id)}?scenario=${encodeURIComponent(published!.id)}`}
                >
                  Review changes
                </Link>
              </LinkButton>
            )}
          </section>
          <section className="space-y-4 border-t border-border pt-5">
            <p className="eyebrow">Portfolio trade-offs</p>
            <h2 className="text-xl font-medium">
              {baselineSelected ? "Current forecast" : "What changes by project"}
            </h2>
            <p className="text-xs text-muted-foreground">
              {baselineSelected
                ? "Computed outcomes for the current plan."
                : "Current plan → selected option. These are forecasts, not completed work."}
            </p>
            <ul className="space-y-3">
              {projectIds.map((id) => {
                const before = record(baseline[id]);
                const after = record(outcomes[id]);
                const delta = baselineSelected
                  ? undefined
                  : finite(record(selected.deltas[id]).delayProbabilityChange);
                const project = analysis.projects?.find((item) => item.id === id);
                const name =
                  text(project?.name) ||
                  text(after.projectName) ||
                  text(before.projectName) ||
                  "Project name unavailable";
                const risk = (value: RecordValue) =>
                  formatPercent(
                    finite(value.delayProbability) ?? finite(value.delayRisk),
                  );
                const finish = (value: RecordValue) =>
                  text(value.finishP50)
                    ? formatDate(text(value.finishP50))
                    : "Finish unknown";
                return (
                  <li key={id} className="border-t border-border pt-3 text-sm">
                    <p className="font-medium">{name}</p>
                    <p className="mt-1 text-muted-foreground">
                      Delay risk:{" "}
                      {baselineSelected
                        ? risk(after)
                        : `${risk(before)} → ${risk(after)}`}
                      {delta !== undefined &&
                        ` (${delta > 0 ? "+" : ""}${Number((delta * 100).toFixed(1))} percentage points)`}
                    </p>
                    <p className="mt-1 text-muted-foreground">
                      Typical finish:{" "}
                      {baselineSelected
                        ? finish(after)
                        : `${finish(before)} → ${finish(after)}`}
                    </p>
                    <p className="mt-1 text-muted-foreground">
                      Finish range:{" "}
                      {baselineSelected
                        ? uncertaintyRange(after)
                        : `${uncertaintyRange(before)} → ${uncertaintyRange(after)}`}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {baselineSelected
                        ? sampleSummary(after)
                        : `Current: ${sampleSummary(before)}. Selected: ${sampleSummary(after)}`}
                    </p>
                  </li>
                );
              })}
            </ul>
            {projectIds.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No project outcomes were returned.
              </p>
            )}
            <p className="text-sm text-muted-foreground">
              Run samples:{" "}
              {analysis.sampleCount ?? analysis.modelInfo?.sampleCount ?? "Not reported"}.
              Samples describe uncertainty within a plan, not separate decisions.
            </p>
            <p className="text-xs leading-5 text-muted-foreground">
              An unknown finish may extend beyond the forecast period. Missing values are
              not zero risk.
            </p>
          </section>
        </SheetContent>
      </Sheet>
    </div>
  );
}
