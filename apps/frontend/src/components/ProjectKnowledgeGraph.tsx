import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { RotateCcw } from "lucide-react";
import { Button } from "./ui/button";
import type { EvidenceClaim, KnowledgeDocument } from "../lib/types";
import { cn } from "../lib/utils";

const WIDTH = 800;
const HEIGHT = 480;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 4;
const DOCUMENT_SOURCE_TYPE = "analysis_summary";
type SemanticLink = {
  source: string;
  target: string;
  similarity: number;
  kind: "related";
};
type KnowledgeDocumentWithProvenance = KnowledgeDocument & {
  provenance?: { sourceType?: string };
};
type Node = {
  id: string;
  label: string;
  document?: KnowledgeDocumentWithProvenance;
  claim?: EvidenceClaim;
  x: number;
  y: number;
};
type DocumentEdge = { id: string; source: Node; target: Node };
type RelatedEdge = DocumentEdge & { similarity: number };

/** Only explicit source identifiers establish document-to-claim relationships. */
function sources(claim: EvidenceClaim): string[] {
  return [
    ...new Set(
      [
        claim.documentId,
        claim.sourceDocumentId,
        typeof claim.source === "object" ? claim.source.documentId : undefined,
      ].filter((id): id is string => Boolean(id)),
    ),
  ];
}

function isAnalysisSummary(document: KnowledgeDocumentWithProvenance): boolean {
  return document.provenance?.sourceType === DOCUMENT_SOURCE_TYPE;
}

function documentLabel(document: KnowledgeDocumentWithProvenance): string {
  if (isAnalysisSummary(document)) return "Analysis";
  return document.name || document.fileName || document.id;
}

function ellipsize(value: string, maxChars: number): string {
  if (value.length <= maxChars) return value;
  return `${value.slice(0, Math.max(1, maxChars - 1))}…`;
}

/** Wrap a graph caption onto a few short lines so titles stay readable under nodes. */
function wrapNodeLabel(label: string, maxChars = 18, maxLines = 2): string[] {
  const words = label.replace(/\s+/g, " ").trim().split(" ").filter(Boolean);
  if (!words.length) return [];
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (candidate.length <= maxChars) {
      line = candidate;
      continue;
    }
    if (line) lines.push(line);
    line = word;
    if (lines.length === maxLines - 1) break;
  }
  if (lines.length < maxLines && line) lines.push(line);
  const last = lines.length - 1;
  if (last < 0) return lines;
  if (lines[last].length > maxChars || lines.join(" ") !== words.join(" ")) {
    lines[last] = ellipsize(lines[last], maxChars);
  }
  return lines;
}

function NodeCaption({
  lines,
  y,
  emphasis,
}: {
  lines: string[];
  y: number;
  emphasis: boolean;
}) {
  if (!lines.length) return null;
  return (
    <text
      y={y}
      textAnchor="middle"
      fill={emphasis ? "var(--foreground)" : "var(--muted-foreground)"}
      fontSize={11}
      fontWeight={emphasis ? 500 : 400}
      letterSpacing="0.01em"
      stroke="var(--background)"
      strokeWidth={3}
      paintOrder="stroke"
    >
      {lines.map((line, index) => (
        <tspan key={`${index}-${line}`} x={0} dy={index === 0 ? 0 : 13}>
          {line}
        </tspan>
      ))}
    </text>
  );
}

function nodeKey(kind: "document" | "claim", id: string): string {
  return JSON.stringify([kind, id]);
}

function clampSimilarity(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

/**
 * Displays the relationship graph without turning relationship discovery into an
 * action. Source edges stay visually primary; semantic links are deliberately subtle.
 */
export function ProjectKnowledgeGraph({
  documents,
  claims,
  semanticLinks = [],
  scopeLabel,
  toolbar,
  selectedDocumentId,
  selectedClaimId,
  onSelectDocument,
  onSelectClaim,
}: {
  documents: KnowledgeDocumentWithProvenance[];
  claims: EvidenceClaim[];
  semanticLinks?: SemanticLink[];
  scopeLabel?: string;
  toolbar?: ReactNode;
  selectedDocumentId?: string;
  selectedClaimId?: string;
  onSelectDocument?: (id: string) => void;
  onSelectClaim?: (id: string) => void;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const [selected, setSelected] = useState<string>();
  const [revealedDocumentId, setRevealedDocumentId] = useState<string>();
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: number; x: number; y: number } | null>(null);
  const {
    documents: documentNodes,
    nodes,
    sourceEdges,
    relatedEdges,
  } = useMemo(() => {
    const all: Node[] = [
      ...documents.map((document) => ({
        id: nodeKey("document", document.id),
        label: documentLabel(document),
        document,
        x: 0,
        y: 0,
      })),
      ...claims.map((claim) => ({
        id: nodeKey("claim", claim.id),
        label: claim.summary || claim.assertion || claim.id,
        claim,
        x: 0,
        y: 0,
      })),
    ];
    const docs = all.filter((node) => node.document);
    const findings = all.filter(
      (node) =>
        node.claim &&
        revealedDocumentId &&
        sources(node.claim).includes(revealedDocumentId),
    );
    const docMap = new Map<string, Node>();
    docs.forEach((node, index) => {
      const angle = index * 2.39996;
      const radius = docs.length === 1 ? 0 : 96 + 46 * Math.sqrt(index);
      node.x = WIDTH / 2 + Math.cos(angle) * radius;
      node.y = HEIGHT / 2 + Math.sin(angle) * radius;
      if (node.document) docMap.set(node.document.id, node);
    });

    const sourceEdges: DocumentEdge[] = [];
    findings.forEach((node, index) => {
      const linked = sources(node.claim!).flatMap((id) => {
        const document = docMap.get(id);
        return document ? [document] : [];
      });
      const anchor = linked[0];
      const angle = index * 2.39996;
      const radius = anchor ? 78 + (index % 3) * 32 : 196 + (index % 3) * 28;
      node.x = (anchor?.x ?? WIDTH / 2) + Math.cos(angle) * radius;
      node.y = (anchor?.y ?? HEIGHT / 2) + Math.sin(angle) * radius;
      linked.forEach((source) =>
        sourceEdges.push({
          id: `${source.id}:${node.id}`,
          source,
          target: node,
        }),
      );
    });

    const relatedEdges: RelatedEdge[] = [];
    const relatedPairs = new Set<string>();
    semanticLinks.forEach((link) => {
      if (link.kind !== "related" || link.source === link.target) return;
      const source = docMap.get(link.source);
      const target = docMap.get(link.target);
      if (!source || !target) return;
      const pair = [link.source, link.target].sort().join(":");
      if (relatedPairs.has(pair)) return;
      relatedPairs.add(pair);
      relatedEdges.push({
        id: `related:${pair}`,
        source,
        target,
        similarity: clampSimilarity(link.similarity),
      });
    });

    return {
      documents: docs,
      nodes: [...docs, ...findings],
      sourceEdges,
      relatedEdges,
    };
  }, [claims, documents, revealedDocumentId, semanticLinks]);

  useEffect(() => {
    if (selectedClaimId) {
      setSelected(nodeKey("claim", selectedClaimId));
    } else if (selectedDocumentId) {
      setSelected(nodeKey("document", selectedDocumentId));
    } else {
      setSelected(undefined);
    }
    if (selectedDocumentId) setRevealedDocumentId(selectedDocumentId);
  }, [selectedClaimId, selectedDocumentId]);

  useEffect(() => {
    if (selected && !nodes.some((node) => node.id === selected)) setSelected(undefined);
  }, [nodes, selected]);

  useEffect(() => {
    if (
      revealedDocumentId &&
      !documents.some((document) => document.id === revealedDocumentId)
    ) {
      setRevealedDocumentId(undefined);
    }
  }, [documents, revealedDocumentId]);

  function select(node: Node) {
    setSelected(node.id);
    if (node.document) {
      setRevealedDocumentId(node.document.id);
      onSelectDocument?.(node.document.id);
    }
    if (node.claim) onSelectClaim?.(node.claim.id);
  }

  function zoom(factor: number) {
    setView((current) => {
      const scale = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, current.scale * factor));
      const ratio = scale / current.scale;
      return {
        scale,
        x: WIDTH / 2 - (WIDTH / 2 - current.x) * ratio,
        y: HEIGHT / 2 - (HEIGHT / 2 - current.y) * ratio,
      };
    });
  }

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handleWheel = (event: WheelEvent) => {
      if (!event.deltaY) return;
      event.preventDefault();
      const bounds = svg.getBoundingClientRect();
      const screenPoint = svg.createSVGPoint();
      screenPoint.x = event.clientX;
      screenPoint.y = event.clientY;
      const transform = svg.getScreenCTM();
      const point = transform
        ? screenPoint.matrixTransform(transform.inverse())
        : {
            x: ((event.clientX - bounds.left) / bounds.width) * WIDTH,
            y: ((event.clientY - bounds.top) / bounds.height) * HEIGHT,
          };
      const factor = Math.exp(-event.deltaY / 600);
      setView((current) => {
        const scale = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, current.scale * factor));
        const ratio = scale / current.scale;
        return {
          scale,
          x: point.x - (point.x - current.x) * ratio,
          y: point.y - (point.y - current.y) * ratio,
        };
      });
    };
    svg.addEventListener("wheel", handleWheel, { passive: false });
    return () => svg.removeEventListener("wheel", handleWheel);
  }, []);

  return (
    <section
      aria-labelledby={titleId}
      className="min-w-0 overflow-hidden rounded-xl bg-card p-4 ring-1 ring-foreground/10 sm:p-6 lg:p-8"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2 id={titleId} className="sr-only">
          Knowledge graph{scopeLabel ? ` · ${scopeLabel}` : ""}
        </h2>
        <div className="min-w-0 flex-1">{toolbar}</div>
        <div className="flex gap-1" aria-label="Graph controls" role="group">
          <Button
            className="size-12"
            variant="outline"
            size="icon"
            aria-label="Zoom out"
            onClick={() => zoom(0.8)}
          >
            −
          </Button>
          <Button
            className="size-12"
            variant="outline"
            size="icon"
            aria-label="Zoom in"
            onClick={() => zoom(1.25)}
          >
            +
          </Button>
          <Button
            className="size-12"
            variant="ghost"
            size="icon"
            aria-label="Reset graph"
            onClick={() => setView({ x: 0, y: 0, scale: 1 })}
          >
            <RotateCcw className="size-4" />
          </Button>
        </div>
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="mt-5 h-[min(66vh,42rem)] min-h-[28rem] w-full touch-none rounded-lg border border-border bg-background sm:min-h-[34rem]"
        aria-describedby={descriptionId}
        aria-label="Knowledge relationship graph"
        role="group"
        onPointerDown={(event) => {
          if (event.button !== 0 || drag.current) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
        }}
        onPointerMove={(event) => {
          const previous = drag.current;
          if (!previous || previous.id !== event.pointerId) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          const scale = Math.min(bounds.width / WIDTH, bounds.height / HEIGHT);
          if (!scale) return;
          const dx = (event.clientX - previous.x) / scale;
          const dy = (event.clientY - previous.y) / scale;
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
        <title id={descriptionId}>
          Source documents and claims are selectable. Dashed lines show related documents.
        </title>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
          <g aria-hidden="true">
            {relatedEdges.map((edge) => (
              <line
                key={edge.id}
                x1={edge.source.x}
                y1={edge.source.y}
                x2={edge.target.x}
                y2={edge.target.y}
                stroke="var(--muted-foreground)"
                strokeDasharray="6 7"
                strokeOpacity={0.18 + edge.similarity * 0.22}
                strokeWidth={1.5}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {sourceEdges.map((edge) => (
              <line
                key={edge.id}
                x1={edge.source.x}
                y1={edge.source.y}
                x2={edge.target.x}
                y2={edge.target.y}
                stroke="var(--border)"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </g>
          {nodes.map((node) => (
            <g
              key={node.id}
              transform={`translate(${node.x} ${node.y})`}
              role="button"
              tabIndex={0}
              aria-label={`${node.document ? (isAnalysisSummary(node.document) ? "Analysis" : "Document") : "Claim"}: ${node.label}`}
              aria-pressed={selected === node.id}
              className="group cursor-pointer"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={() => select(node)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  select(node);
                }
              }}
            >
              <title>{node.label}</title>
              <circle r={40} fill="transparent" />
              <circle
                r={node.document ? 13 : 9}
                fill={node.document ? "var(--foreground)" : "var(--primary)"}
                stroke={selected === node.id ? "var(--ring)" : "var(--card)"}
                strokeWidth={3}
                className={cn(
                  "transition-[stroke-width] group-focus:stroke-ring group-focus:stroke-[5]",
                  selected === node.id && "stroke-[5]",
                )}
              />
              <NodeCaption
                lines={wrapNodeLabel(node.label, node.document ? 18 : 16)}
                y={node.document ? 28 : 22}
                emphasis={Boolean(node.document) || selected === node.id}
              />
            </g>
          ))}
        </g>
        {!nodes.length && (
          <text
            x={WIDTH / 2}
            y={HEIGHT / 2}
            textAnchor="middle"
            fill="var(--muted-foreground)"
          >
            No knowledge imported
          </text>
        )}
      </svg>
      <details className="mt-4 rounded-lg border border-border/70">
        <summary className="min-h-11 cursor-pointer px-3 py-3 text-sm font-medium">
          Browse documents
        </summary>
        <ul className="max-h-56 space-y-1 overflow-y-auto border-t border-border/70 p-2">
          {documentNodes.map((node) => (
            <li key={node.id}>
              <button
                type="button"
                className={cn(
                  "min-h-11 w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring",
                  selected === node.id && "bg-muted",
                )}
                aria-pressed={selected === node.id}
                onClick={() => select(node)}
              >
                {node.label}
              </button>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
