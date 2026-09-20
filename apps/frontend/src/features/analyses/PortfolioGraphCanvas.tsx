import { Canvas } from "@react-three/fiber";
import { Html, Line, OrbitControls, Text } from "@react-three/drei";
import { Button } from "@/components/ui/button";
import { useMemo, useState } from "react";
import {
  graphEdgeStyle,
  normalizeGraphEdgeType,
  normalizeGraphNodeType,
  type GraphLayout,
  type GraphSelection,
  type GraphVector,
  type NormalizedPortfolioGraph,
  type PortfolioGraphEdge,
  type PortfolioGraphNode,
} from "./graph";

export interface PortfolioGraphCanvasProps {
  graph: NormalizedPortfolioGraph;
  layout: GraphLayout;
  selection: GraphSelection;
  onSelect: (selection: Exclude<GraphSelection, null>) => void;
  focusIds?: string[];
}

interface GraphPalette {
  background: string;
  text: string;
  border: string;
  primary: string;
  resource: string;
  warning: string;
  muted: string;
  project: string;
  task: string;
  worker: string;
  material: string;
}

function cssColor(variable: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(variable)
    .trim();
  return value || fallback;
}

function useGraphPalette(): GraphPalette {
  return useMemo(
    () => ({
      background: cssColor("--card", "white"),
      text: cssColor("--foreground", "black"),
      border: cssColor("--border", "lightgray"),
      primary: cssColor("--primary", "coral"),
      resource: cssColor("--chart-2", "seagreen"),
      warning: cssColor("--chart-4", "goldenrod"),
      muted: cssColor("--muted-foreground", "gray"),
      project: cssColor("--chart-5", "black"),
      task: cssColor("--primary", "coral"),
      worker: cssColor("--chart-2", "seagreen"),
      material: cssColor("--chart-4", "goldenrod"),
    }),
    [],
  );
}

function nodeColor(node: PortfolioGraphNode, palette: GraphPalette): string {
  switch (normalizeGraphNodeType(node.type)) {
    case "project":
      return palette.project;
    case "worker":
      return palette.worker;
    case "material":
      return palette.material;
    case "task":
      return palette.task;
    default:
      return palette.primary;
  }
}

function edgeColor(edge: PortfolioGraphEdge, palette: GraphPalette): string {
  switch (graphEdgeStyle(edge.type).palette) {
    case "primary":
      return palette.primary;
    case "resource":
      return palette.resource;
    case "warning":
      return palette.warning;
    default:
      return palette.muted;
  }
}

function edgeIsSelected(edge: PortfolioGraphEdge, selection: GraphSelection): boolean {
  return selection?.kind === "edge" && selection.id === edge.id;
}

export default function PortfolioGraphCanvas({
  graph,
  layout,
  selection,
  onSelect,
  focusIds,
}: PortfolioGraphCanvasProps) {
  const palette = useGraphPalette();
  const focused = useMemo(() => (focusIds ? new Set(focusIds) : undefined), [focusIds]);
  const center = useMemo<GraphVector>(
    () => [
      (layout.bounds.minX + layout.bounds.maxX) / 2,
      (layout.bounds.minY + layout.bounds.maxY) / 2,
      (layout.bounds.minZ + layout.bounds.maxZ) / 2,
    ],
    [layout],
  );
  const cameraPosition = useMemo<GraphVector>(() => {
    const width = layout.bounds.maxX - layout.bounds.minX;
    const depth = layout.bounds.maxZ - layout.bounds.minZ;
    return [
      center[0] + Math.max(12, width * 0.95),
      center[1] +
        Math.max(12, depth * 0.8, (layout.bounds.maxY - layout.bounds.minY) * 1.6),
      center[2] + Math.max(14, depth * 1.1 + 10),
    ];
  }, [layout, center]);

  return (
    <Canvas
      aria-label="Interactive three-dimensional portfolio relationship graph"
      camera={{ position: cameraPosition, fov: 42, near: 0.1, far: 1000 }}
      dpr={[1, 2]}
      frameloop="demand"
      gl={{ antialias: true, alpha: true }}
      onCreated={({ gl }) => {
        gl.setClearColor(palette.background, 0);
      }}
    >
      <ambientLight intensity={1.35} />
      <directionalLight position={[6, 10, 8]} intensity={2.1} />
      <directionalLight position={[-5, 4, -8]} intensity={0.55} />
      <GraphLaneGuides layout={layout} palette={palette} />
      {graph.edges.map((edge) => {
        const source = layout.positions[edge.source];
        const target = layout.positions[edge.target];
        if (!source || !target) return null;
        return (
          <GraphEdgeLine
            key={edge.id}
            edge={edge}
            source={source.coordinates}
            target={target.coordinates}
            palette={palette}
            selected={edgeIsSelected(edge, selection)}
            dimmed={Boolean(
              focused && !focused.has(edge.source) && !focused.has(edge.target),
            )}
            onSelect={onSelect}
          />
        );
      })}
      {graph.nodes.map((node) => {
        const position = layout.positions[node.id];
        if (!position) return null;
        return (
          <GraphNodeMesh
            key={node.id}
            node={node}
            position={position.coordinates}
            palette={palette}
            selected={selection?.kind === "node" && selection.id === node.id}
            dimmed={Boolean(focused && !focused.has(node.id))}
            labelByDefault={
              node.type === "project" || (!focused && graph.nodes.length <= 12)
            }
            onSelect={onSelect}
          />
        );
      })}
      <OrbitControls
        makeDefault
        target={center}
        enableDamping
        enablePan
        minDistance={5}
        maxDistance={Math.max(70, (layout.bounds.maxZ - layout.bounds.minZ) * 4)}
        dampingFactor={0.12}
        rotateSpeed={0.65}
        panSpeed={0.65}
      />
    </Canvas>
  );
}

function GraphLaneGuides({
  layout,
  palette,
}: {
  layout: GraphLayout;
  palette: GraphPalette;
}) {
  const start: GraphVector = [layout.bounds.minX - 1.2, -3.25, 0];
  const end: GraphVector = [layout.bounds.maxX + 1.2, -3.25, 0];
  return (
    <group>
      {layout.lanes.map((lane) => (
        <group key={lane.id}>
          <Line
            points={[
              [start[0], start[1], lane.depth],
              [end[0], end[1], lane.depth],
            ]}
            color={palette.border}
            lineWidth={0.7}
            transparent
            opacity={0.65}
          />
          <Text
            position={[layout.bounds.minX - 1.15, -2.7, lane.depth]}
            anchorX="right"
            anchorY="middle"
            color={palette.muted}
            fontSize={0.27}
            maxWidth={2.2}
          >
            {lane.name || lane.id}
          </Text>
        </group>
      ))}
    </group>
  );
}

function GraphEdgeLine({
  edge,
  source,
  target,
  palette,
  selected,
  dimmed,
  onSelect,
}: {
  edge: PortfolioGraphEdge;
  source: GraphVector;
  target: GraphVector;
  palette: GraphPalette;
  selected: boolean;
  dimmed: boolean;
  onSelect: (selection: Exclude<GraphSelection, null>) => void;
}) {
  const style = graphEdgeStyle(edge.type);
  const dashed = style.stroke !== "solid";
  return (
    <Line
      points={[source, target]}
      color={edgeColor(edge, palette)}
      lineWidth={selected ? 2.5 : style.stroke === "solid" ? 1.7 : 1.25}
      dashed={dashed}
      dashSize={style.stroke === "dot" ? 0.08 : 0.34}
      gapSize={style.stroke === "dot" ? 0.22 : 0.18}
      transparent
      opacity={selected ? 1 : dimmed ? 0.15 : 0.72}
      onClick={(event) => {
        event.stopPropagation();
        onSelect({ kind: "edge", id: edge.id });
      }}
      onPointerDown={(event) => event.stopPropagation()}
    />
  );
}

function GraphNodeMesh({
  node,
  position,
  palette,
  selected,
  dimmed,
  labelByDefault,
  onSelect,
}: {
  node: PortfolioGraphNode;
  position: GraphVector;
  palette: GraphPalette;
  selected: boolean;
  dimmed: boolean;
  labelByDefault: boolean;
  onSelect: (selection: Exclude<GraphSelection, null>) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const color = nodeColor(node, palette);
  const scale = selected ? 1.16 : hovered ? 1.06 : 1;
  if (normalizeGraphNodeType(node.type) === "project") {
    return (
      <group position={position}>
        <Html center zIndexRange={[30, 0]}>
          <Button
            variant={selected ? "default" : "outline"}
            className="h-auto min-h-20 w-36 flex-col items-start gap-1 whitespace-normal border-2 px-3 py-3 text-left shadow-sm"
            style={{ opacity: dimmed ? 0.6 : 1 }}
            aria-label={`Show work for ${node.label}`}
            aria-pressed={selected}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              onSelect({ kind: "node", id: node.id });
            }}
          >
            <span className="text-[10px] uppercase tracking-wide opacity-75">
              Project
            </span>
            <span className="text-sm font-semibold leading-5">{node.label}</span>
            <span className="text-xs opacity-75">Show work →</span>
          </Button>
        </Html>
      </group>
    );
  }
  return (
    <group
      position={position}
      scale={scale}
      onClick={(event) => {
        event.stopPropagation();
        onSelect({ kind: "node", id: node.id });
      }}
      onPointerDown={(event) => event.stopPropagation()}
      onPointerOver={() => setHovered(true)}
      onPointerOut={() => setHovered(false)}
    >
      <mesh castShadow>
        <NodeGeometry nodeType={normalizeGraphNodeType(node.type)} />
        <meshStandardMaterial
          color={color}
          transparent={dimmed}
          opacity={dimmed ? 0.22 : 1}
          emissive={selected ? color : palette.background}
          emissiveIntensity={selected ? 0.24 : 0.04}
          roughness={0.72}
          metalness={0.08}
        />
      </mesh>
      {(labelByDefault || selected || hovered) && (
        <Text
          position={[0, 0.75, 0]}
          anchorX="center"
          anchorY="middle"
          color={dimmed ? palette.muted : palette.text}
          fillOpacity={dimmed ? 0.45 : 1}
          depthOffset={-1}
          fontSize={0.27}
          maxWidth={3.2}
          outlineColor={palette.background}
          outlineWidth={0.018}
        >
          {node.label}
        </Text>
      )}
    </group>
  );
}

function NodeGeometry({ nodeType }: { nodeType: string }) {
  switch (nodeType) {
    case "project":
      return <boxGeometry args={[0.78, 0.78, 0.78]} />;
    case "worker":
      return <octahedronGeometry args={[0.5, 0]} />;
    case "material":
      return <cylinderGeometry args={[0.46, 0.46, 0.62, 6]} />;
    case "task":
      return <sphereGeometry args={[0.43, 18, 14]} />;
    default:
      return <dodecahedronGeometry args={[0.45, 0]} />;
  }
}
