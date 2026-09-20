import type { Outcome, ProjectOutcome, Strategy } from "./types";

const STRATEGY_ROLE_LABELS: Record<string, string> = {
  baseline: "Current plan",
  fast: "Fast",
  balanced: "Balanced",
  safe: "Safe",
};

export function formatDate(
  value?: string,
  options: Intl.DateTimeFormatOptions = {
    day: "numeric",
    month: "short",
    year: "numeric",
  },
): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat("en-GB", options).format(date);
}

export function formatDateTime(value?: string): string {
  return formatDate(value, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatPercent(value?: number): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value * 100)}%`
    : "—";
}

export function formatDays(value?: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const rounded = Number(value.toFixed(1));
  return `${rounded} ${rounded === 1 ? "day" : "days"}`;
}

export function formatHours(value?: number): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const rounded = Number(value.toFixed(1));
  return `${rounded} ${rounded === 1 ? "hour" : "hours"}`;
}

export function formatMetric(value?: number, suffix = ""): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Not available";
  return `${Number(value.toFixed(1))}${suffix}`;
}

export function formatAssumptionValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Not specified";
  if (typeof value !== "object") return String(value);
  const record = value as Record<string, unknown>;
  const optimistic =
    typeof record.optimistic === "number" ? record.optimistic : undefined;
  const mostLikely =
    typeof record.mostLikely === "number" ? record.mostLikely : undefined;
  const pessimistic =
    typeof record.pessimistic === "number" ? record.pessimistic : undefined;
  if (optimistic !== undefined || mostLikely !== undefined || pessimistic !== undefined) {
    const unit = typeof record.unit === "string" ? record.unit : "person-hours";
    return `Optimistic ${formatMetric(optimistic)} · most likely ${formatMetric(mostLikely)} · pessimistic ${formatMetric(pessimistic)} ${unit}`;
  }
  return "Structured assumption returned";
}

export function formatOutcomeDate(value?: string, missingReason?: string): string {
  if (value) return formatDate(value);
  return missingReason || "Beyond simulated horizon";
}

export function humanize(value?: string): string {
  if (!value) return "—";
  return value
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter: string) => letter.toUpperCase());
}

export function strategyRoleLabels(
  strategy: Pick<Strategy, "strategy" | "strategyRoles" | "labels">,
): string[] {
  const roles = [
    ...(strategy.strategyRoles || []),
    strategy.strategy || "",
    ...(strategy.labels || []),
  ];
  const labels = roles
    .map((role) => STRATEGY_ROLE_LABELS[role.toLowerCase()] || role)
    .filter((role): role is string => Boolean(role) && role !== "recommended")
    .filter((role) => ["Current plan", "Fast", "Balanced", "Safe"].includes(role));
  return [...new Set(labels)];
}

export function initials(name?: string, fallback = "IQ"): string {
  if (!name) return fallback;
  return name
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export function outcomeForProject(
  outcomes: Record<string, Outcome> | Array<Outcome & { projectId?: string }> | undefined,
  projectId: string,
): Outcome | undefined {
  if (!outcomes) return undefined;
  if (Array.isArray(outcomes))
    return outcomes.find((outcome) => outcome.projectId === projectId);
  return outcomes[projectId];
}

export function projectOutcomeFor(
  outcomes:
    | Record<string, ProjectOutcome>
    | Array<ProjectOutcome & { projectId?: string }>
    | undefined,
  projectId: string,
): ProjectOutcome | undefined {
  if (!outcomes) return undefined;
  if (Array.isArray(outcomes))
    return outcomes.find((outcome) => outcome.projectId === projectId);
  return outcomes[projectId];
}
