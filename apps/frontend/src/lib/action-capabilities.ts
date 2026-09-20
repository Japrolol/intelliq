import type { Strategy } from "./types";

const dateActions = new Set([
  "date_shift",
  "schedule_shift",
  "weather_shift",
  "material_shift",
]);

export function isTimecueExecutable(strategy: Strategy): boolean {
  return (
    Boolean(strategy.actions?.length) &&
    strategy.actions!.every(
      (action) =>
        typeof action.type === "string" &&
        dateActions.has(action.type) &&
        Boolean(action.taskId && action.startsAt && action.endsAt),
    )
  );
}
