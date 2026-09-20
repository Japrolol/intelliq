import { useEffect, useId, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ActionParameterList } from "./ActionParameterList";
import { Button } from "./ui";
import { formatDate, formatPercent } from "../lib/format";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "./ui/sheet";
import { cn } from "../lib/utils";
import type { JobResponse } from "../lib/types";

interface SimulationNode {
  id: string;
  label: string;
  status: "running" | "completed" | "rejected";
  completedSamples: number;
  totalSamples: number;
  feasible?: boolean;
  outcomes?: Array<{
    projectId: string;
    projectName: string;
    delayProbability?: number;
    finishP50?: string;
    unfinishedCount?: number;
  }>;
  actions?: Array<Record<string, unknown>>;
}

type SimulationJob = JobResponse & {
  simulationProgress?: {
    samplesPerCandidate: number;
    totalCandidates: number;
    completedCandidates: number;
    nodes: SimulationNode[];
  };
};

const BASELINE_ID = "baseline";
const STAGE_LABELS: Record<string, string> = {
  queued: "Waiting to start",
  authenticating: "Connecting to Timecue",
  authorizing: "Checking access",
  reading_timecue: "Reading projects, tasks and calendars",
  validating_inputs: "Checking planning inputs",
  reviewing_evidence: "Reviewing project evidence",
  fetching_context: "Checking weather and travel context",
  simulating: "Comparing possible actions",
  explaining: "Preparing the recommendation",
  publishing: "Saving your decisions",
};

const STATUS_LABELS = {
  running: "Evaluating",
  completed: "Evaluated",
  rejected: "Rejected",
} as const;

const MAX_NODES = 63;
const NODE_WIDTH = 220;
const NODE_HEIGHT = 100;
const ROW_GAP = 168;
const COLUMN_GAP = 248;
const TREE_PAD = 24;
const MIN_ZOOM = 0.05;
const MAX_ZOOM = 2;
const CANVAS_HEIGHT = 420;

interface Viewport {
  x: number;
  y: number;
  scale: number;
}

interface PositionedNode {
  node: SimulationNode;
  heapIndex: number;
  parentIndex?: number;
  x: number;
  y: number;
}

function layoutGrowingTree(nodes: SimulationNode[]): PositionedNode[] {
  if (!nodes.length) return [];
  const maxDepth = Math.floor(Math.log2(nodes.length));
  return nodes.map((node, heapIndex) => {
    const depth = Math.floor(Math.log2(heapIndex + 1));
    const indexInLevel = heapIndex - (2 ** depth - 1);
    const spacing = COLUMN_GAP * 2 ** (maxDepth - depth);
    return {
      node,
      heapIndex,
      parentIndex: heapIndex === 0 ? undefined : Math.floor((heapIndex - 1) / 2),
      x: TREE_PAD + (indexInLevel + 0.5) * spacing - NODE_WIDTH / 2,
      y: TREE_PAD + depth * ROW_GAP,
    };
  });
}

function branchPath(parent: PositionedNode, child: PositionedNode): string {
  const x1 = parent.x + NODE_WIDTH / 2;
  const y1 = parent.y + NODE_HEIGHT;
  const x2 = child.x + NODE_WIDTH / 2;
  const y2 = child.y;
  const midY = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
}

/** Only reported nodes enter the canvas; camera movement never represents progress. */
export function LiveSimulationTree({
  job,
  onSelectNode,
  saved = false,
}: {
  job?: SimulationJob;
  onSelectNode?: (id: string) => void;
  saved?: boolean;
}) {
  const progress = job?.simulationProgress;
  const reported = progress?.nodes || [];
  const root = reported.find((node) => node.id === BASELINE_ID);
  const branches = reported
    .filter((node) => node.id !== BASELINE_ID)
    .slice(0, MAX_NODES - (root ? 1 : 0));
  const positioned = layoutGrowingTree(root ? [root, ...branches] : branches);
  const active = positioned.find(({ node }) => node.status === "running");
  const [view, setView] = useState<Viewport>({ x: 0, y: 0, scale: 1 });
  const [selectedId, setSelectedId] = useState<string>();
  const selected = reported.find((node) => node.id === selectedId);
  const [follow, setFollow] = useState(true);
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: number; x: number; y: number } | null>(null);
  const reducedMotion = useReducedMotion();
  const spring = reducedMotion
    ? { duration: 0 }
    : { type: "spring" as const, bounce: 0, duration: 0.4 };
  const descriptionId = useId();
  const resultsId = useId();
  const activeX = active?.x;
  const activeY = active?.y;

  useEffect(() => {
    if (!saved) return;
    fitGraph();
    const svg = svgRef.current;
    if (!svg) return;
    const observer = new ResizeObserver(() => fitGraph());
    observer.observe(svg);
    return () => observer.disconnect();
  }, [saved, reported.length]);

  // Follow branch transitions, not every sample tick; dragging takes camera control.
  useEffect(() => {
    if (!follow || activeX === undefined || activeY === undefined) return;
    const width = svgRef.current?.getBoundingClientRect().width || 600;
    setView((current) => ({
      ...current,
      x: width / 2 - (activeX + NODE_WIDTH / 2) * current.scale,
      y: CANVAS_HEIGHT / 2 - (activeY + NODE_HEIGHT / 2) * current.scale,
    }));
  }, [activeX, activeY, follow]);

  /** Zoom around a viewport point so the node under the pointer stays anchored. */
  function zoom(factor: number, x?: number, y = CANVAS_HEIGHT / 2) {
    setFollow(false);
    const centerX = x ?? (svgRef.current?.getBoundingClientRect().width || 600) / 2;
    setView((current) => {
      const scale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, current.scale * factor));
      const ratio = scale / current.scale;
      return {
        scale,
        x: centerX - (centerX - current.x) * ratio,
        y: y - (y - current.y) * ratio,
      };
    });
  }

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const bounds = svg.getBoundingClientRect();
      const delta =
        event.deltaY *
        (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? bounds.height : 1);
      zoom(
        Math.exp(-delta * 0.002),
        event.clientX - bounds.left,
        event.clientY - bounds.top,
      );
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, []);

  function fitGraph() {
    setFollow(false);
    const width = svgRef.current?.getBoundingClientRect().width || 600;
    const right = Math.max(
      NODE_WIDTH + 48,
      ...positioned.map(({ x }) => x + NODE_WIDTH + 24),
    );
    const bottom = Math.max(
      NODE_HEIGHT + 48,
      ...positioned.map(({ y }) => y + NODE_HEIGHT + 24),
    );
    const scale = Math.min(1, width / right, CANVAS_HEIGHT / bottom);
    setView({
      scale,
      x: (width - right * scale) / 2,
      y: (CANVAS_HEIGHT - bottom * scale) / 2,
    });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2" role="status" aria-live="polite">
        <h2 className="text-xl font-semibold">Preparing decisions</h2>
        <p className="text-sm text-muted-foreground">
          {saved
            ? "Evaluated options"
            : STAGE_LABELS[job?.stage || ""] || "Waiting for analysis progress"}
        </p>
        {progress && (
          <p className="text-xs tabular-nums text-muted-foreground">
            {progress.completedCandidates} of {progress.totalCandidates} options evaluated
            {" · "}
            {progress.samplesPerCandidate} samples per option
          </p>
        )}
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-background">
        <div className="flex flex-wrap items-center gap-1 border-b border-border p-2">
          <Button
            variant="quiet"
            className="size-11 min-h-11 p-0"
            aria-label="Zoom out"
            onClick={() => zoom(0.8)}
          >
            −
          </Button>
          <Button
            variant="quiet"
            className="size-11 min-h-11 p-0"
            aria-label="Zoom in"
            onClick={() => zoom(1.25)}
          >
            +
          </Button>
          <Button variant="quiet" className="min-h-11" onClick={fitGraph}>
            Fit graph
          </Button>
          {!saved && (
            <Button
              variant="quiet"
              className="min-h-11"
              aria-pressed={follow}
              onClick={() => setFollow(!follow)}
            >
              Follow current option
            </Button>
          )}
        </div>
        <svg
          ref={svgRef}
          height={CANVAS_HEIGHT}
          className="w-full touch-none cursor-grab active:cursor-grabbing"
          role="group"
          aria-label="Live simulation option graph"
          aria-describedby={descriptionId}
          onPointerDown={(event) => {
            if (event.button !== 0 || drag.current) return;
            setFollow(false);
            event.currentTarget.setPointerCapture(event.pointerId);
            drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
          }}
          onPointerMove={(event) => {
            const previous = drag.current;
            if (!previous || previous.id !== event.pointerId) return;
            const dx = event.clientX - previous.x;
            const dy = event.clientY - previous.y;
            drag.current = { id: previous.id, x: event.clientX, y: event.clientY };
            setView((current) => ({ ...current, x: current.x + dx, y: current.y + dy }));
          }}
          onPointerUp={() => {
            drag.current = null;
          }}
          onPointerCancel={() => {
            drag.current = null;
          }}
          onLostPointerCapture={() => {
            drag.current = null;
          }}
        >
          <desc id={descriptionId}>
            Drag to pan. Use the wheel or buttons to zoom. Select an option to inspect
            reported outcomes. Rows expand 2, 4, 8, 16 as more options are evaluated.
            Connectors show the growing tree, not a sequence of applied changes.
          </desc>
          <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
            {positioned.map((item) => {
              if (item.parentIndex === undefined) return null;
              const parent = positioned[item.parentIndex];
              if (!parent) return null;
              return (
                <motion.path
                  key={`edge-${item.node.id}`}
                  d={branchPath(parent, item)}
                  fill="none"
                  className={cn(
                    "stroke-border",
                    item.node.status === "running" && "stroke-primary",
                  )}
                  strokeWidth={item.node.status === "running" ? 2 : 1}
                  vectorEffect="non-scaling-stroke"
                  initial={reducedMotion ? false : { opacity: 0 }}
                  animate={{ opacity: 1, d: branchPath(parent, item) }}
                  transition={spring}
                />
              );
            })}
            {positioned.map(({ node, x, y }) => (
              <motion.g
                key={node.id}
                initial={reducedMotion ? false : { opacity: 0, x, y }}
                animate={{ opacity: 1, x, y }}
                transition={spring}
              >
                <foreignObject width={NODE_WIDTH} height={NODE_HEIGHT}>
                  <button
                    type="button"
                    className={cn(
                      "flex h-full w-full flex-col justify-center gap-1 rounded-xl border bg-card px-3 text-left text-card-foreground outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                      node.status === "running" ? "border-primary" : "border-border",
                      selectedId === node.id && "bg-muted",
                    )}
                    aria-pressed={selectedId === node.id}
                    aria-haspopup="dialog"
                    aria-controls={resultsId}
                    title={node.label}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() =>
                      onSelectNode ? onSelectNode(node.id) : setSelectedId(node.id)
                    }
                  >
                    <span className="line-clamp-2 text-sm font-medium">
                      {node.id === BASELINE_ID ? "Current plan" : node.label}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {STATUS_LABELS[node.status]}
                    </span>
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {node.completedSamples} / {node.totalSamples} samples
                    </span>
                  </button>
                </foreignObject>
              </motion.g>
            ))}
          </g>
          {reported.length === 0 && (
            <svg
              aria-hidden="true"
              width="100%"
              height={CANVAS_HEIGHT}
              viewBox="0 0 600 420"
            >
              <g className="fill-muted stroke-border">
                <path
                  d="M 300 92 C 300 130, 170 130, 170 168 M 300 92 C 300 130, 430 130, 430 168 M 170 232 C 170 268, 100 268, 100 308 M 170 232 C 170 268, 240 268, 240 308 M 430 232 C 430 268, 360 268, 360 308 M 430 232 C 430 268, 500 268, 500 308"
                  fill="none"
                />
                <rect x="210" y="28" width="180" height="64" rx="12" />
                <rect x="80" y="168" width="180" height="64" rx="12" />
                <rect x="340" y="168" width="180" height="64" rx="12" />
                <rect x="36" y="308" width="128" height="52" rx="12" />
                <rect x="176" y="308" width="128" height="52" rx="12" />
                <rect x="296" y="308" width="128" height="52" rx="12" />
                <rect x="436" y="308" width="128" height="52" rx="12" />
              </g>
            </svg>
          )}
        </svg>
      </div>
      <p className="text-xs text-muted-foreground">
        {reported.length === 0
          ? "Options appear as a growing tree when evaluation starts. "
          : "Drag to pan · scroll to zoom · select a node for outcomes. "}
        Interim results; your current plan stays unchanged.
      </p>
      {reported.length > MAX_NODES && (
        <p className="text-xs text-muted-foreground">
          Showing up to {MAX_NODES} reported nodes.
        </p>
      )}
      <Sheet
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) setSelectedId(undefined);
        }}
      >
        {selected && (
          <SheetContent id={resultsId} className="overflow-y-auto p-6">
            <SheetHeader className="pr-10">
              <SheetTitle>
                {selected.id === BASELINE_ID ? "Current plan" : selected.label}
              </SheetTitle>
              <SheetDescription>
                {STATUS_LABELS[selected.status]} · {selected.completedSamples} /{" "}
                {selected.totalSamples} samples. These are simulation inputs, not applied
                changes.
              </SheetDescription>
            </SheetHeader>
            <section className="space-y-3">
              <h3 className="text-sm font-medium">Parameters tested</h3>
              {!selected.actions?.length && (
                <p className="text-sm text-muted-foreground">
                  {selected.id === BASELINE_ID
                    ? "Current plan without candidate changes."
                    : "No action parameters reported yet."}
                </p>
              )}
              {selected.actions && selected.actions.length > 0 && (
                <ActionParameterList
                  actions={selected.actions}
                  references={selected.outcomes?.map((outcome) => ({
                    id: outcome.projectId,
                    name: outcome.projectName,
                  }))}
                />
              )}
            </section>
            {selected.status !== "running" && selected.feasible !== undefined && (
              <p className="text-xs text-muted-foreground">
                {selected.feasible
                  ? "Meets evaluated constraints."
                  : "Does not meet evaluated constraints."}
              </p>
            )}
            <h3 className="text-sm font-medium">Evaluated outcomes</h3>
            {(selected.status === "running" || !selected.outcomes?.length) && (
              <p className="text-sm text-muted-foreground">
                {selected.status === "running"
                  ? "Outcomes will be available after evaluation."
                  : "No evaluated outcomes were reported."}
              </p>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              {selected.status !== "running" &&
                selected.outcomes?.map((outcome) => (
                  <div
                    key={outcome.projectId}
                    className="space-y-1 text-xs text-muted-foreground"
                  >
                    <p className="text-sm font-medium text-foreground">
                      {outcome.projectName || "Project outcome"}
                    </p>
                    {typeof outcome.delayProbability === "number" && (
                      <p>Delay probability: {formatPercent(outcome.delayProbability)}</p>
                    )}
                    {outcome.finishP50 && (
                      <p>Median finish: {formatDate(outcome.finishP50)}</p>
                    )}
                    {typeof outcome.unfinishedCount === "number" && (
                      <p>Unfinished samples: {outcome.unfinishedCount}</p>
                    )}
                  </div>
                ))}
            </div>
          </SheetContent>
        )}
      </Sheet>
    </div>
  );
}
