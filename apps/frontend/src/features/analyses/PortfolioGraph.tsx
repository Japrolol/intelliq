import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Button } from "@/components/ui";
import {
  buildStableGraphLayout,
  graphDataText,
  nodeProjectId,
  nodeTypeLabel,
  normalizeGraph,
  normalizeGraphEdgeType,
  type GraphLayout,
  type GraphSelection,
  type NormalizedPortfolioGraph,
  type PortfolioGraphAnalysis,
  type PortfolioGraphContract,
  type PortfolioGraphEdge,
  type PortfolioGraphNode,
} from "./graph";

const LazyPortfolioGraphCanvas = lazy(() => import("./PortfolioGraphCanvas"));

export type { PortfolioGraphAnalysis, PortfolioGraphContract } from "./graph";

export interface PortfolioGraphProps {
  graph: PortfolioGraphContract;
  analysis: PortfolioGraphAnalysis;
  scenarioId?: string;
}

type GraphViewMode = "3d" | "list";

/** Keep source identifiers for selection, while giving every view a readable label. */
function ownerGraph(graph: PortfolioGraphContract): NormalizedPortfolioGraph {
  const normalized = normalizeGraph(graph);
  const nodes = normalized.nodes.map((node) => {
    const suppliedName = graphDataText(node.data, ["name", "title", "displayName"]);
    const label = [node.label, suppliedName].find(
      (value) => value && value !== node.id && !isTechnicalLabel(value),
    );
    return { ...node, label: label || `${nodeTypeLabel(node)} name unavailable` };
  });
  return {
    ...normalized,
    nodes,
    projects: normalized.projects.map((project) => {
      const projectNode = nodes.find(
        (node) => node.type === "project" && nodeProjectId(node) === project.id,
      );
      const name = project.name;
      return {
        ...project,
        name:
          name && name !== project.id && !isTechnicalLabel(name)
            ? name
            : projectNode?.label || "Project name unavailable",
      };
    }),
  };
}

function isTechnicalLabel(value: string): boolean {
  return (
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(value) ||
    /^(project|task|worker|material|source):/i.test(value)
  );
}

/** Add breathing room between project lanes without changing membership or order. */
function ownerGraphLayout(graph: NormalizedPortfolioGraph): GraphLayout {
  const layout = buildStableGraphLayout({
    ...graph,
    // Collapsed project links describe work-level dependencies, not a project DAG.
    edges: graph.edges.filter(
      (edge) => edge.type !== "prerequisite" || !edge.data?.ownerConnectionSummary,
    ),
  });
  const depthScale = 1.3;
  return {
    ...layout,
    positions: Object.fromEntries(
      Object.entries(layout.positions).map(([id, position]) => [
        id,
        {
          ...position,
          coordinates: [
            position.coordinates[0],
            position.coordinates[1],
            position.coordinates[2] * depthScale,
          ] as [number, number, number],
        },
      ]),
    ),
    lanes: layout.lanes.map((lane) => ({ ...lane, depth: lane.depth * depthScale })),
    bounds: {
      ...layout.bounds,
      minZ: layout.bounds.minZ * depthScale,
      maxZ: layout.bounds.maxZ * depthScale,
    },
  };
}

function supportsWebGL(): boolean {
  if (typeof document === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    return Boolean(
      canvas.getContext("webgl2") ||
      canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl"),
    );
  } catch {
    return false;
  }
}

/** Collapse hidden work into its actual parent project; every line retains a source edge. */
function visiblePortfolioGraph(
  graph: NormalizedPortfolioGraph,
  projectId?: string,
): NormalizedPortfolioGraph {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const projectNodes = new Map(
    graph.nodes
      .filter((node) => node.type === "project")
      .map((node) => [nodeProjectId(node), node]),
  );
  const resourceProjects = new Map<string, Set<string>>();
  for (const edge of graph.edges) {
    for (const [resourceId, neighborId] of [
      [edge.source, edge.target],
      [edge.target, edge.source],
    ]) {
      const resource = byId.get(resourceId);
      const neighbor = byId.get(neighborId);
      if (!resource || !neighbor || !["worker", "material"].includes(resource.type))
        continue;
      const linkedProject = nodeProjectId(neighbor);
      if (!linkedProject) continue;
      const projects = resourceProjects.get(resourceId) || new Set<string>();
      projects.add(linkedProject);
      resourceProjects.set(resourceId, projects);
    }
  }
  const nodes = graph.nodes.filter((node) => {
    if (node.type === "project") return true;
    if (projectId && nodeProjectId(node) === projectId) return true;
    const linked = resourceProjects.get(node.id);
    return projectId ? Boolean(linked?.has(projectId)) : (linked?.size || 0) > 1;
  });
  const visible = new Set(nodes.map((node) => node.id));
  const endpoint = (id: string) => {
    if (visible.has(id)) return id;
    const node = byId.get(id);
    return node ? projectNodes.get(nodeProjectId(node))?.id : undefined;
  };
  const grouped = new Map<string, PortfolioGraphEdge>();
  for (const edge of graph.edges) {
    const source = endpoint(edge.source);
    const target = endpoint(edge.target);
    if (!source || !target || source === target) continue;
    const key = `${source}|${target}|${edge.type}`;
    if (grouped.has(key)) continue;
    const collapsed = source !== edge.source || target !== edge.target;
    grouped.set(key, {
      ...edge,
      source,
      target,
      data: {
        ...edge.data,
        ownerConnectionSummary: collapsed
          ? `Includes ${byId.get(edge.source)?.label || "work"} → ${byId.get(edge.target)?.label || "work"}. Select a project to see the underlying work.`
          : undefined,
      },
    });
  }
  return { ...graph, nodes, edges: [...grouped.values()] };
}

function selectionExists(
  graph: NormalizedPortfolioGraph,
  selection: GraphSelection,
): boolean {
  if (!selection) return false;
  return selection.kind === "node"
    ? graph.nodes.some((node) => node.id === selection.id)
    : graph.edges.some((edge) => edge.id === selection.id);
}

export function PortfolioGraph({ graph }: PortfolioGraphProps) {
  const normalizedGraph = useMemo(() => ownerGraph(graph), [graph]);
  const [focusedProjectId, setFocusedProjectId] = useState<string>();
  const visibleGraph = useMemo(
    () => visiblePortfolioGraph(normalizedGraph, focusedProjectId),
    [normalizedGraph, focusedProjectId],
  );
  const layout = useMemo(() => ownerGraphLayout(visibleGraph), [visibleGraph]);
  const webGLAvailable = useMemo(() => supportsWebGL(), []);
  const [viewMode, setViewMode] = useState<GraphViewMode>(() =>
    supportsWebGL() ? "3d" : "list",
  );
  const [selection, setSelection] = useState<GraphSelection>(null);
  const [fitVersion, setFitVersion] = useState(0);

  useEffect(() => {
    if (!webGLAvailable && viewMode === "3d") setViewMode("list");
  }, [viewMode, webGLAvailable]);

  useEffect(() => {
    if (selection && !selectionExists(visibleGraph, selection)) {
      setSelection(null);
    }
  }, [visibleGraph, selection]);

  const selectEntity = (next: Exclude<GraphSelection, null>) => {
    setSelection(next);
    const node =
      next.kind === "node"
        ? normalizedGraph.nodes.find((candidate) => candidate.id === next.id)
        : undefined;
    if (node?.type === "project") {
      setFocusedProjectId(nodeProjectId(node));
      setFitVersion((version) => version + 1);
    }
  };
  const focusIds = focusedProjectId
    ? visibleGraph.nodes
        .filter(
          (node) =>
            nodeProjectId(node) === focusedProjectId ||
            node.type === "worker" ||
            node.type === "material",
        )
        .map((node) => node.id)
    : undefined;

  const activeViewMode = !webGLAvailable && viewMode === "3d" ? "list" : viewMode;
  if (normalizedGraph.nodes.length === 0) {
    return (
      <section
        aria-labelledby="portfolio-relationship-graph-title"
        className="overflow-hidden rounded-2xl border bg-card shadow-sm"
        data-testid="portfolio-graph"
      >
        <div className="flex min-h-56 flex-col justify-center gap-2 p-6 sm:p-8">
          <p className="text-xs font-semibold tracking-[0.16em] text-muted-foreground uppercase">
            Relationship explorer
          </p>
          <h2 id="portfolio-relationship-graph-title">No portfolio relationships yet</h2>
          <p className="max-w-xl text-sm text-muted-foreground">
            The analysis did not return graph entities, so there is no relationship to
            inspect.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="portfolio-relationship-graph-title"
      className="overflow-hidden rounded-2xl border bg-card shadow-sm"
      data-testid="portfolio-graph"
    >
      <header className="flex flex-col gap-5 border-b px-5 py-5 sm:px-7 sm:py-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <h2 id="portfolio-relationship-graph-title" className="mt-2">
            Your projects and people
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Select a project to reveal its work and shared people. Lines show existing
            dependencies and shared resources.
          </p>
        </div>
        <div
          className="flex flex-wrap items-center gap-2"
          role="group"
          aria-label="Graph view"
        >
          <Button
            size="sm"
            variant={activeViewMode === "3d" ? "primary" : "secondary"}
            aria-pressed={activeViewMode === "3d"}
            disabled={!webGLAvailable}
            onClick={() => setViewMode("3d")}
          >
            3D
          </Button>
          <Button
            size="sm"
            variant={activeViewMode === "list" ? "primary" : "secondary"}
            aria-pressed={activeViewMode === "list"}
            onClick={() => setViewMode("list")}
          >
            List
          </Button>
          <Button
            size="sm"
            variant="quiet"
            disabled={activeViewMode !== "3d"}
            onClick={() => setFitVersion((version) => version + 1)}
          >
            Fit view
          </Button>
        </div>
      </header>

      {!webGLAvailable && (
        <div
          className="border-b bg-muted/40 px-5 py-3 text-sm text-muted-foreground sm:px-7"
          role="status"
        >
          3D is unavailable in this browser. Select a project from the list to see its
          work and people.
        </div>
      )}

      <div
        className="flex flex-wrap gap-2 border-b px-5 py-3 sm:px-7"
        aria-label="Projects in this plan"
      >
        {focusedProjectId && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setFocusedProjectId(undefined);
              setSelection(null);
              setFitVersion((version) => version + 1);
            }}
          >
            All projects
          </Button>
        )}
        {normalizedGraph.projects.map((project) => {
          const node = normalizedGraph.nodes.find(
            (candidate) =>
              candidate.type === "project" && nodeProjectId(candidate) === project.id,
          );
          return (
            <Button
              key={project.id}
              size="sm"
              variant="quiet"
              disabled={!node}
              aria-pressed={Boolean(
                node && selection?.kind === "node" && selection.id === node.id,
              )}
              onClick={() => node && selectEntity({ kind: "node", id: node.id })}
            >
              {project.name}
            </Button>
          );
        })}
      </div>

      <div className="min-w-0 p-4 sm:p-6">
        <div className="min-w-0">
          {activeViewMode === "3d" ? (
            <GraphCanvasRegion
              graph={visibleGraph}
              layout={layout}
              selection={selection}
              fitVersion={fitVersion}
              onSelect={selectEntity}
              focusIds={focusIds}
            />
          ) : (
            <GraphListView
              graph={visibleGraph}
              selection={selection}
              onSelect={selectEntity}
            />
          )}

          {activeViewMode === "3d" && <GraphLegend />}
        </div>
      </div>
    </section>
  );
}

function GraphCanvasRegion({
  graph,
  layout,
  selection,
  fitVersion,
  onSelect,
  focusIds,
}: {
  graph: NormalizedPortfolioGraph;
  layout: GraphLayout;
  selection: GraphSelection;
  fitVersion: number;
  onSelect: (selection: Exclude<GraphSelection, null>) => void;
  focusIds?: string[];
}) {
  const accessibleFallback = (
    <div className="flex min-h-[24rem] flex-col gap-4 rounded-xl border bg-muted/20 p-4 sm:min-h-[30rem] sm:p-5">
      <div className="rounded-lg border border-amber-600/30 bg-amber-50/60 p-4 text-sm text-amber-900">
        The 3D canvas could not be started. The same graph is available as an accessible
        list.
      </div>
      <GraphListView graph={graph} selection={selection} onSelect={onSelect} />
    </div>
  );
  return (
    <GraphCanvasErrorBoundary resetKey={fitVersion} fallback={accessibleFallback}>
      <Suspense fallback={<GraphLoadingState />}>
        <div className="h-[28rem] min-h-[24rem] min-w-0 touch-none overflow-hidden rounded-xl border bg-muted/20 sm:h-[34rem]">
          <LazyPortfolioGraphCanvas
            key={fitVersion}
            graph={graph}
            layout={layout}
            selection={selection}
            onSelect={onSelect}
            focusIds={focusIds}
          />
        </div>
      </Suspense>
    </GraphCanvasErrorBoundary>
  );
}

function GraphLoadingState() {
  return (
    <div
      className="flex h-[28rem] min-h-[24rem] min-w-0 flex-col gap-4 rounded-xl border bg-muted/20 p-5 sm:h-[34rem]"
      role="status"
      aria-label="Preparing relationship graph"
    >
      <div className="h-5 w-44 animate-pulse rounded bg-muted" />
      <div className="grid flex-1 grid-cols-3 gap-4 opacity-60">
        <div className="animate-pulse rounded-xl bg-muted" />
        <div className="animate-pulse rounded-xl bg-muted" />
        <div className="animate-pulse rounded-xl bg-muted" />
      </div>
      <span className="sr-only">Preparing the relationship graph.</span>
    </div>
  );
}

interface GraphCanvasErrorBoundaryProps {
  children: ReactNode;
  fallback: ReactNode;
  resetKey: number;
}

interface GraphCanvasErrorBoundaryState {
  hasError: boolean;
}

class GraphCanvasErrorBoundary extends Component<
  GraphCanvasErrorBoundaryProps,
  GraphCanvasErrorBoundaryState
> {
  state: GraphCanvasErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): GraphCanvasErrorBoundaryState {
    return { hasError: true };
  }

  componentDidUpdate(previousProps: GraphCanvasErrorBoundaryProps) {
    if (previousProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false });
    }
  }

  render() {
    return this.state.hasError ? this.props.fallback : this.props.children;
  }
}

function GraphLegend() {
  return (
    <ul
      className="mt-3 flex flex-wrap gap-x-5 gap-y-2 px-1 text-xs text-muted-foreground"
      aria-label="Relationship legend"
    >
      <li className="inline-flex items-center gap-2">
        <span className="w-5 border-t-2 border-primary" aria-hidden="true" />
        Must finish first
      </li>
      <li className="inline-flex items-center gap-2">
        <span
          className="w-5 border-t-2 border-dashed border-chart-2"
          aria-hidden="true"
        />
        Assigned to work
      </li>
      <li className="inline-flex items-center gap-2">
        <span
          className="w-5 border-t-2 border-dotted border-chart-2"
          aria-hidden="true"
        />
        Shared person
      </li>
      <li className="inline-flex items-center gap-2">
        <span
          className="w-5 border-t-2 border-dashed border-chart-4"
          aria-hidden="true"
        />
        Material needed
      </li>
    </ul>
  );
}

function GraphListView({
  graph,
  selection,
  onSelect,
}: {
  graph: NormalizedPortfolioGraph;
  selection: GraphSelection;
  onSelect: (selection: Exclude<GraphSelection, null>) => void;
}) {
  const projects = graph.nodes.filter((node) => node.type === "project");
  return (
    <ul className="divide-y divide-border" aria-label="Projects and their work">
      {projects.map((project) => {
        const projectId = nodeProjectId(project);
        const related = new Set(
          graph.edges.flatMap((edge) => {
            if (edge.source === project.id) return [edge.target];
            if (edge.target === project.id) return [edge.source];
            return [];
          }),
        );
        const work = graph.nodes.filter(
          (node) =>
            node.id !== project.id &&
            node.type !== "project" &&
            (nodeProjectId(node) === projectId || related.has(node.id)),
        );
        return (
          <li key={project.id} className="py-3 first:pt-0">
            <Button
              variant="secondary"
              className="min-h-12 w-full justify-between whitespace-normal px-4 text-left"
              aria-pressed={selection?.kind === "node" && selection.id === project.id}
              onClick={() => onSelect({ kind: "node", id: project.id })}
            >
              <span>{project.label}</span>
              <span className="ml-3 shrink-0 text-xs">Show work →</span>
            </Button>
            {work.length > 0 && (
              <ul className="mt-3 space-y-2 pl-4 text-sm text-muted-foreground">
                {work.map((node) => (
                  <li key={node.id}>
                    {node.label} <span className="text-xs">· {nodeTypeLabel(node)}</span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
      {projects.length === 0 && (
        <li className="py-4 text-sm text-muted-foreground">
          No project records were returned.
        </li>
      )}
    </ul>
  );
}

export default PortfolioGraph;
