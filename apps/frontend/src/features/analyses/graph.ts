/**
 * Shared, presentation-only helpers for the portfolio relationship explorer.
 *
 * The API contract intentionally keeps `data` extensible: the graph owns
 * stable entity IDs and typed relationships, while scenario outcomes remain
 * references to the canonical analysis result. These helpers therefore read
 * only known display fields and never manufacture a simulation trace.
 */

export type GraphData = Record<string, unknown>;

export interface PortfolioGraphNode {
  id: string;
  type: string;
  label: string;
  projectId?: string | null;
  data?: GraphData;
}

export interface PortfolioGraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  data?: GraphData;
}

export interface PortfolioGraphProject {
  id: string;
  name?: string;
}

/** Structural shape of GET /analyses/{id}/graph. */
export interface PortfolioGraphContract {
  nodes: readonly PortfolioGraphNode[];
  edges: readonly PortfolioGraphEdge[];
  projects?: readonly PortfolioGraphProject[];
}

/** Minimal structural shape accepted for the existing Analysis response. */
export interface PortfolioGraphAnalysis {
  id?: string;
  projectId?: string;
  baselineByProject?: Record<string, unknown>;
  strategies?: readonly unknown[];
}

export interface NormalizedPortfolioGraph {
  nodes: PortfolioGraphNode[];
  edges: PortfolioGraphEdge[];
  projects: PortfolioGraphProject[];
}

export type GraphVector = [number, number, number];

export interface GraphPosition {
  coordinates: GraphVector;
  lane: number;
  laneId: string;
  rank: number;
  row: number;
}

export interface GraphLane extends PortfolioGraphProject {
  depth: number;
  index: number;
}

export interface GraphLayout {
  positions: Record<string, GraphPosition>;
  lanes: GraphLane[];
  maxRank: number;
  bounds: {
    minX: number;
    maxX: number;
    minY: number;
    maxY: number;
    minZ: number;
    maxZ: number;
  };
}

export type GraphSelection =
  { kind: "node"; id: string } | { kind: "edge"; id: string } | null;

export type GraphEdgePalette = "primary" | "resource" | "warning" | "muted";
export type GraphEdgeStroke = "solid" | "dash" | "dot";

export interface GraphEdgeVisualStyle {
  palette: GraphEdgePalette;
  stroke: GraphEdgeStroke;
}

export interface GraphEffectField {
  key: string;
  label: string;
  value: string;
}

export interface GraphScenarioEffect {
  hasEffect: boolean;
  scenarioLabel: string;
  summary?: string;
  values: GraphEffectField[];
}

const LANE_GAP = 4.2;
const RANK_GAP = 4.8;

const EFFECT_FIELDS: ReadonlyArray<{ key: string; label: string }> = [
  { key: "summary", label: "Effect" },
  { key: "impact", label: "Impact" },
  { key: "status", label: "Status" },
  { key: "delayProbability", label: "Delay risk" },
  { key: "delayRisk", label: "Delay risk" },
  { key: "risk", label: "Risk" },
  { key: "expectedPositiveDelayDays", label: "Expected positive delay" },
  { key: "finishP50", label: "Finish p50" },
  { key: "finishDate", label: "Finish date" },
  { key: "bufferDays", label: "Target buffer" },
  { key: "bufferDeltaDays", label: "Buffer change" },
  { key: "changed", label: "Changed" },
];

const SCENARIO_CONTAINER_KEYS = [
  "scenarioEffects",
  "effectsByScenario",
  "scenarioOutcomes",
  "outcomesByScenario",
  "effects",
] as const;

export function graphRecord(value: unknown): GraphData | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return undefined;
  return value as GraphData;
}

export function graphString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function graphNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function graphStringList(value: unknown): string[] {
  if (typeof value === "string" && value.trim()) return [value.trim()];
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === "string" && item.trim()) return [item.trim()];
    const itemRecord = graphRecord(item);
    const label = itemRecord
      ? graphString(itemRecord.name) ||
        graphString(itemRecord.label) ||
        graphString(itemRecord.id)
      : undefined;
    return label ? [label] : [];
  });
}

export function graphDataValue(
  data: GraphData | undefined,
  keys: readonly string[],
): unknown {
  if (!data) return undefined;
  for (const key of keys) {
    if (data[key] !== undefined && data[key] !== null) return data[key];
  }
  return undefined;
}

export function graphDataText(
  data: GraphData | undefined,
  keys: readonly string[],
): string | undefined {
  return graphString(graphDataValue(data, keys));
}

export function normalizeGraphNodeType(type: string): string {
  return type
    .trim()
    .toLowerCase()
    .replace(/[-\s]+/g, "_");
}

export function normalizeGraphEdgeType(type: string): string {
  return normalizeGraphNodeType(type);
}

export function humanizeGraphToken(value: string | undefined): string {
  if (!value) return "Entity";
  const humanized = value
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return humanized ? humanized.charAt(0).toUpperCase() + humanized.slice(1) : "Entity";
}

export function nodeProjectId(node: PortfolioGraphNode): string | undefined {
  if (graphString(node.projectId)) return graphString(node.projectId);
  const projectId = graphDataText(node.data, ["projectId", "project_id"]);
  if (projectId) return projectId;
  if (normalizeGraphNodeType(node.type) === "project") {
    return graphDataText(node.data, ["id", "sourceId", "source_id"]) || node.id;
  }
  return undefined;
}

export function nodeTypeLabel(node: PortfolioGraphNode): string {
  return humanizeGraphToken(node.type);
}

export function nodeDisplayLabel(node: PortfolioGraphNode): string {
  return (
    graphString(node.label) ||
    graphDataText(node.data, ["name", "title", "displayName", "display_name"]) ||
    node.id
  );
}

export function normalizeGraph(graph: PortfolioGraphContract): NormalizedPortfolioGraph {
  const nodeMap = new Map<string, PortfolioGraphNode>();
  for (const node of graph.nodes || []) {
    const id = graphString(node.id);
    if (!id || nodeMap.has(id)) continue;
    nodeMap.set(id, {
      ...node,
      id,
      type: graphString(node.type) || "entity",
      label: nodeDisplayLabel(node),
      projectId: nodeProjectId(node),
      data: graphRecord(node.data) || {},
    });
  }

  const edges = (graph.edges || []).flatMap((edge) => {
    const id = graphString(edge.id);
    const source = graphString(edge.source);
    const target = graphString(edge.target);
    if (!id || !source || !target || !nodeMap.has(source) || !nodeMap.has(target)) {
      return [];
    }
    return [
      {
        ...edge,
        id,
        source,
        target,
        type: graphString(edge.type) || "relationship",
        data: graphRecord(edge.data) || {},
      },
    ];
  });

  const projectMap = new Map<string, PortfolioGraphProject>();
  for (const project of graph.projects || []) {
    const id = graphString(project.id);
    if (!id || projectMap.has(id)) continue;
    projectMap.set(id, { id, name: graphString(project.name) || id });
  }
  for (const node of nodeMap.values()) {
    const projectId = nodeProjectId(node);
    if (!projectId || projectMap.has(projectId)) continue;
    projectMap.set(projectId, { id: projectId, name: projectId });
  }

  return {
    nodes: [...nodeMap.values()],
    edges,
    projects: [...projectMap.values()],
  };
}

function laneForNode(
  node: PortfolioGraphNode,
  laneIds: ReadonlySet<string>,
  fallbackLaneId: string,
): string {
  const projectId = nodeProjectId(node);
  return projectId && laneIds.has(projectId) ? projectId : fallbackLaneId;
}

function addResourceNeighbor(
  neighbors: Map<string, Set<string>>,
  source: string,
  target: string,
) {
  const sourceNeighbors = neighbors.get(source) || new Set<string>();
  sourceNeighbors.add(target);
  neighbors.set(source, sourceNeighbors);
  const targetNeighbors = neighbors.get(target) || new Set<string>();
  targetNeighbors.add(source);
  neighbors.set(target, targetNeighbors);
}

/**
 * Creates a deterministic layout. Project lanes are depth (`z`), prerequisite
 * rank is horizontal (`x`), and row order is derived from stable IDs/labels.
 * Resource edges never participate in prerequisite ranking.
 */
export function buildStableGraphLayout(graph: NormalizedPortfolioGraph): GraphLayout {
  const sortedNodes = [...graph.nodes].sort((left, right) =>
    left.id.localeCompare(right.id),
  );
  const laneMap = new Map<string, PortfolioGraphProject>();
  for (const project of graph.projects) {
    if (!laneMap.has(project.id)) laneMap.set(project.id, project);
  }
  const fallbackLaneId = laneMap.keys().next().value || "portfolio";
  if (!laneMap.has(fallbackLaneId))
    laneMap.set(fallbackLaneId, { id: fallbackLaneId, name: "Portfolio" });
  const laneIds = new Set(laneMap.keys());
  const lanes = [...laneMap.values()].map((project, index) => ({
    ...project,
    name: project.name || project.id,
    depth: index * LANE_GAP,
    index,
  }));
  const laneIndexById = new Map(lanes.map((lane) => [lane.id, lane.index]));

  const ranks = new Map(sortedNodes.map((node) => [node.id, 0]));
  const prerequisiteEdges = graph.edges
    .filter((edge) => normalizeGraphEdgeType(edge.type) === "prerequisite")
    .sort((left, right) => left.id.localeCompare(right.id));
  for (let pass = 0; pass < Math.max(1, sortedNodes.length); pass += 1) {
    let changed = false;
    for (const edge of prerequisiteEdges) {
      const sourceRank = ranks.get(edge.source) || 0;
      const targetRank = ranks.get(edge.target) || 0;
      if (sourceRank + 1 <= targetRank) continue;
      ranks.set(edge.target, sourceRank + 1);
      changed = true;
    }
    if (!changed) break;
  }

  const maxRank = Math.max(0, ...ranks.values());
  const positions = new Map<string, GraphPosition>();
  const resourceNeighbors = new Map<string, Set<string>>();
  for (const edge of graph.edges) {
    const edgeType = normalizeGraphEdgeType(edge.type);
    if (edgeType === "assignment" || edgeType === "shared_resource") {
      addResourceNeighbor(resourceNeighbors, edge.source, edge.target);
    }
  }

  const laneIndex = (laneId: string) => laneIndexById.get(laneId) || 0;
  const laneDepth = (laneId: string) => laneIndex(laneId) * LANE_GAP;
  const groupRows = new Map<string, PortfolioGraphNode[]>();
  for (const node of sortedNodes) {
    const type = normalizeGraphNodeType(node.type);
    if (type === "project" || type === "worker" || type === "material") continue;
    const laneId = laneForNode(node, laneIds, fallbackLaneId);
    const groupKey = `${laneId}:${ranks.get(node.id) || 0}`;
    const rows = groupRows.get(groupKey) || [];
    rows.push(node);
    groupRows.set(groupKey, rows);
  }

  for (const nodes of groupRows.values()) {
    nodes.sort(
      (left, right) =>
        nodeDisplayLabel(left).localeCompare(nodeDisplayLabel(right)) ||
        left.id.localeCompare(right.id),
    );
  }
  for (const [groupKey, nodes] of groupRows) {
    const separator = groupKey.lastIndexOf(":");
    const laneId = groupKey.slice(0, separator);
    const rank = Number(groupKey.slice(separator + 1));
    const lane = laneIndex(laneId);
    nodes.forEach((node, row) => {
      positions.set(node.id, {
        coordinates: [
          rank * RANK_GAP,
          (row - (nodes.length - 1) / 2) * 1.35,
          lane * LANE_GAP,
        ],
        lane,
        laneId,
        rank,
        row,
      });
    });
  }

  const projectRows = new Map<string, PortfolioGraphNode[]>();
  for (const node of sortedNodes) {
    if (normalizeGraphNodeType(node.type) !== "project") continue;
    const laneId = laneForNode(node, laneIds, fallbackLaneId);
    const rows = projectRows.get(laneId) || [];
    rows.push(node);
    projectRows.set(laneId, rows);
  }
  for (const [laneId, nodes] of projectRows) {
    nodes.forEach((node, row) => {
      positions.set(node.id, {
        coordinates: [-2.4, (row - (nodes.length - 1) / 2) * 1.35, laneDepth(laneId)],
        lane: laneIndex(laneId),
        laneId,
        rank: -1,
        row,
      });
    });
  }

  const workerNodes = sortedNodes.filter(
    (node) => normalizeGraphNodeType(node.type) === "worker",
  );
  workerNodes.forEach((node, row) => {
    const relatedLanes = [...(resourceNeighbors.get(node.id) || [])]
      .map((neighborId) => graph.nodes.find((candidate) => candidate.id === neighborId))
      .map((neighbor) =>
        neighbor ? laneForNode(neighbor, laneIds, fallbackLaneId) : undefined,
      )
      .filter((laneId): laneId is string => Boolean(laneId));
    const uniqueLaneIndices = [...new Set(relatedLanes.map(laneIndex))];
    const lane = uniqueLaneIndices.length
      ? uniqueLaneIndices.reduce((sum, value) => sum + value, 0) /
        uniqueLaneIndices.length
      : 0;
    const laneId = relatedLanes[0] || fallbackLaneId;
    positions.set(node.id, {
      coordinates: [maxRank * RANK_GAP + 2.8, 2.75 - row * 0.95, lane * LANE_GAP],
      lane,
      laneId,
      rank: maxRank + 1,
      row,
    });
  });

  const materialNodes = sortedNodes.filter(
    (node) => normalizeGraphNodeType(node.type) === "material",
  );
  materialNodes.forEach((node, row) => {
    const laneId = laneForNode(node, laneIds, fallbackLaneId);
    positions.set(node.id, {
      coordinates: [-2.4, -2.4 - row * 0.9, laneDepth(laneId)],
      lane: laneIndex(laneId),
      laneId,
      rank: -1,
      row,
    });
  });

  for (const node of sortedNodes) {
    if (positions.has(node.id)) continue;
    const laneId = laneForNode(node, laneIds, fallbackLaneId);
    const rank = ranks.get(node.id) || 0;
    positions.set(node.id, {
      coordinates: [rank * RANK_GAP, 0, laneDepth(laneId)],
      lane: laneIndex(laneId),
      laneId,
      rank,
      row: 0,
    });
  }

  const coordinateValues = [...positions.values()].map(
    (position) => position.coordinates,
  );
  const xs = coordinateValues.map(([x]) => x);
  const ys = coordinateValues.map(([, y]) => y);
  const zs = coordinateValues.map(([, , z]) => z);
  return {
    positions: Object.fromEntries(positions),
    lanes,
    maxRank,
    bounds: {
      minX: Math.min(...xs, -2.4),
      maxX: Math.max(...xs, maxRank * RANK_GAP + 2.8),
      minY: Math.min(...ys, -2.4),
      maxY: Math.max(...ys, 2.75),
      minZ: Math.min(...zs, 0),
      maxZ: Math.max(...zs, 0),
    },
  };
}

export function graphEdgeStyle(type: string): GraphEdgeVisualStyle {
  switch (normalizeGraphEdgeType(type)) {
    case "prerequisite":
      return { palette: "primary", stroke: "solid" };
    case "assignment":
      return { palette: "resource", stroke: "dash" };
    case "shared_resource":
      return { palette: "resource", stroke: "dot" };
    case "material_requirement":
      return { palette: "warning", stroke: "dash" };
    default:
      return { palette: "muted", stroke: "dot" };
  }
}

function scenarioContainerRecord(
  data: GraphData | undefined,
  scenarioId: string | undefined,
): GraphData | undefined {
  if (!data) return undefined;
  const requestedId = scenarioId || "baseline";
  for (const key of SCENARIO_CONTAINER_KEYS) {
    const container = data[key];
    const record = graphRecord(container);
    if (record) {
      const exact = graphRecord(record[requestedId]);
      if (exact) return exact;
      if (!scenarioId) {
        const baseline = graphRecord(record.baseline) || graphRecord(record.current);
        if (baseline) return baseline;
      }
    }
    if (Array.isArray(container)) {
      const matching = container.find((item) => {
        const itemRecord = graphRecord(item);
        return (
          graphString(itemRecord?.scenarioId) === requestedId ||
          graphString(itemRecord?.scenario_id) === requestedId
        );
      });
      const matchingRecord = graphRecord(matching);
      if (matchingRecord) return matchingRecord;
    }
  }
  if (hasEffectField(data)) return data;
  return undefined;
}

function hasEffectField(data: GraphData | undefined): boolean {
  return Boolean(data && EFFECT_FIELDS.some(({ key }) => data[key] !== undefined));
}

function analysisStrategy(
  analysis: PortfolioGraphAnalysis,
  scenarioId: string | undefined,
): GraphData | undefined {
  if (!scenarioId || scenarioId === "baseline" || scenarioId === "current")
    return undefined;
  return (analysis.strategies || [])
    .map(graphRecord)
    .find((strategy) => graphString(strategy?.id) === scenarioId);
}

function strategyOutcomeForProject(
  analysis: PortfolioGraphAnalysis,
  scenarioId: string | undefined,
  projectId: string | undefined,
): GraphData | undefined {
  if (!projectId) return undefined;
  if (!scenarioId || scenarioId === "baseline" || scenarioId === "current") {
    return graphRecord(analysis.baselineByProject?.[projectId]);
  }
  const strategy = analysisStrategy(analysis, scenarioId);
  if (!strategy) return undefined;
  const outcomeMap = strategy.outcomesByProject ?? strategy.outcomes_by_project;
  if (Array.isArray(outcomeMap)) {
    const match = outcomeMap.find(
      (item) => graphString(graphRecord(item)?.projectId) === projectId,
    );
    const matchRecord = graphRecord(match);
    if (matchRecord) return matchRecord;
  }
  const mappedOutcome = graphRecord(graphRecord(outcomeMap)?.[projectId]);
  if (mappedOutcome) return mappedOutcome;

  const targetProjectId =
    graphString(strategy.targetProjectId) ||
    graphString(strategy.target_project_id) ||
    graphString(strategy.targetId);
  if (
    targetProjectId === projectId ||
    (!targetProjectId && analysis.projectId === projectId)
  ) {
    return graphRecord(strategy.target);
  }
  const donorProjectId =
    graphString(strategy.donorProjectId) ||
    graphString(strategy.donor_project_id) ||
    graphString(strategy.donorId);
  if (donorProjectId === projectId) return graphRecord(strategy.donor);
  return undefined;
}

function scenarioLabel(
  analysis: PortfolioGraphAnalysis,
  scenarioId: string | undefined,
): string {
  if (!scenarioId || scenarioId === "baseline" || scenarioId === "current") {
    return "Current plan";
  }
  const strategy = analysisStrategy(analysis, scenarioId);
  return graphString(strategy?.label) || graphString(strategy?.name) || scenarioId;
}

function mergeEffectData(
  direct: GraphData | undefined,
  outcome: GraphData | undefined,
): GraphData | undefined {
  if (!direct && !outcome) return undefined;
  return { ...(outcome || {}), ...(direct || {}) };
}

export function formatGraphValue(key: string, value: unknown): string | undefined {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number" && Number.isFinite(value)) {
    const lowerKey = key.toLowerCase();
    if (
      (lowerKey.includes("probability") ||
        lowerKey === "risk" ||
        lowerKey.includes("risk")) &&
      value >= 0 &&
      value <= 1
    ) {
      return `${Math.round(value * 100)}%`;
    }
    if (lowerKey.includes("day")) return `${value} ${value === 1 ? "day" : "days"}`;
    if (lowerKey.includes("hour")) return `${value} ${value === 1 ? "hour" : "hours"}`;
    return String(value);
  }
  if (typeof value !== "string" || !value.trim()) return undefined;
  if (/(at|date|p50)$/i.test(key)) {
    const date = new Date(value);
    if (!Number.isNaN(date.valueOf())) {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
    }
  }
  return value.trim();
}

export function getScenarioEffect(
  data: GraphData | undefined,
  analysis: PortfolioGraphAnalysis,
  scenarioId?: string,
  projectId?: string,
): GraphScenarioEffect {
  const direct = scenarioContainerRecord(data, scenarioId);
  const outcome = strategyOutcomeForProject(analysis, scenarioId, projectId);
  const merged = mergeEffectData(direct, outcome);
  const values = merged
    ? EFFECT_FIELDS.flatMap(({ key, label }) => {
        const value = formatGraphValue(key, merged[key]);
        return value ? [{ key, label, value }] : [];
      })
    : [];
  const summary = graphString(merged?.summary) || graphString(merged?.impact);
  return {
    hasEffect: Boolean(merged),
    scenarioLabel: scenarioLabel(analysis, scenarioId),
    summary,
    values,
  };
}

export function getEntitySource(data: GraphData | undefined): string | undefined {
  const source = graphDataValue(data, [
    "sourceRef",
    "sourceReference",
    "sourceId",
    "timecueId",
    "timecue_id",
  ]);
  if (typeof source === "string") return source;
  const sourceRecord = graphRecord(source);
  return sourceRecord
    ? graphString(sourceRecord.id) || graphString(sourceRecord.reference)
    : undefined;
}

export function getEntityWindow(data: GraphData | undefined): string | undefined {
  const start = graphDataText(data, [
    "startsAt",
    "startAt",
    "plannedStartAt",
    "earliestStartAt",
  ]);
  const end = graphDataText(data, ["endsAt", "endAt", "plannedEndAt", "latestFinishAt"]);
  if (!start && !end) return undefined;
  const formattedStart = start ? formatGraphValue("startAt", start) : undefined;
  const formattedEnd = end ? formatGraphValue("endAt", end) : undefined;
  return [formattedStart, formattedEnd].filter(Boolean).join(" → ");
}

export function getEntitySkills(data: GraphData | undefined): string[] {
  return graphStringList(
    graphDataValue(data, [
      "skills",
      "skillNames",
      "specialties",
      "specialty",
      "requiredSpecialty",
    ]),
  );
}

export function getEntityEffort(data: GraphData | undefined): string | undefined {
  const effort = graphDataValue(data, [
    "remainingPersonHours",
    "effortHours",
    "personHours",
    "estimatedPersonHours",
  ]);
  const effortRecord = graphRecord(effort);
  if (effortRecord) {
    const mostLikely = graphNumber(effortRecord.mostLikely);
    if (mostLikely !== undefined) return formatGraphValue("hours", mostLikely);
    const summary = ["optimistic", "pessimistic"].flatMap((key) => {
      const value = graphNumber(effortRecord[key]);
      return value === undefined ? [] : [`${key} ${formatGraphValue("hours", value)}`];
    });
    return summary.length ? summary.join(" · ") : undefined;
  }
  return formatGraphValue("hours", effort);
}
