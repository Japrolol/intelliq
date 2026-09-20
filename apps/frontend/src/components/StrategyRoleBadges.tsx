import { strategyRoleLabels } from "../lib/format";
import type { Strategy } from "../lib/types";
import { Badge } from "./ui";

export function StrategyRoleBadges({ strategy }: { strategy: Strategy }) {
  const roles = strategyRoleLabels(strategy);
  const hasUnchangedBaselineRole = [
    ...(strategy.strategyRoles || []),
    ...(strategy.labels || []),
    strategy.strategy || "",
  ].some((role) => role.toLowerCase() === "unchangedbaseline");
  const displayRoles = hasUnchangedBaselineRole
    ? ["Current plan", ...roles.filter((role) => role !== "Current plan")]
    : roles;
  if (!displayRoles.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label="Policy roles">
      {displayRoles.map((role) => (
        <Badge key={role} tone={role === "Current plan" ? "neutral" : "fixture"}>
          {role}
        </Badge>
      ))}
      {displayRoles.length > 1 && (
        <span className="text-xs text-muted-foreground">Same evaluated plan</span>
      )}
    </div>
  );
}
