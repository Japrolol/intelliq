import { formatDateTime } from "./format";

/** Explain frozen actions without changing their hours, dates, or ordering. */
export function describeActions(
  actions: Array<Record<string, unknown>>,
  projects: Array<{ id: string; name: string }> = [],
): string[] {
  const names = new Map(projects.map((project) => [project.id, project.name]));
  const sentences: string[] = [];
  const overtime = new Map<string, { name: string; hours: number; shifts: string[] }>();
  const label = (value: unknown, fallback: string) =>
    typeof value === "string" && value.trim() ? value : fallback;
  for (const action of actions) {
    const worker = label(action.workerName, "the assigned worker");
    if (action.type === "overtime") {
      const key = label(action.workerId, worker);
      const group = overtime.get(key) ?? { name: worker, hours: 0, shifts: [] };
      const hours = typeof action.hours === "number" ? action.hours : undefined;
      group.hours += hours ?? 0;
      const start =
        typeof action.startsAt === "string"
          ? formatDateTime(action.startsAt)
          : "the planned time";
      const end =
        typeof action.endsAt === "string" ? ` to ${formatDateTime(action.endsAt)}` : "";
      group.shifts.push(
        `${hours === undefined ? "Extra time" : `${hours} hours`} on ${start}${end}`,
      );
      overtime.set(key, group);
    } else if (action.type === "priority" || action.type === "resequence") {
      const order = Array.isArray(action.projectOrderNames)
        ? action.projectOrderNames.filter(
            (name): name is string => typeof name === "string",
          )
        : Array.isArray(action.projectOrder)
          ? action.projectOrder.flatMap((id) =>
              typeof id === "string" && names.has(id) ? [names.get(id)!] : [],
            )
          : [];
      sentences.push(
        order.length
          ? `Give available capacity to these projects in order: ${order.join(" → ")}. Tasks still wait for their prerequisites.`
          : "Change which ready work gets priority. Tasks still wait for their prerequisites.",
      );
    } else if (action.type === "transfer") {
      sentences.push(
        `Move ${worker} from ${label(action.fromProjectName, "the current site")} to ${label(action.toProjectName, "the receiving site")}.`,
      );
    } else if (
      ["date_shift", "schedule_shift", "weather_shift", "material_shift"].includes(
        String(action.type),
      )
    ) {
      sentences.push(
        `Schedule ${label(action.taskName, "the selected task")} from ${typeof action.startsAt === "string" ? formatDateTime(action.startsAt) : "the proposed start"} to ${typeof action.endsAt === "string" ? formatDateTime(action.endsAt) : "the proposed finish"}.`,
      );
    } else if (action.type === "material") {
      sentences.push(
        `Update material availability for ${label(action.taskName, "the selected task")}; an earlier delivery is not guaranteed.`,
      );
    } else if (action.type === "weather") {
      sentences.push(
        "Adjust work to the recorded weather window; suitable conditions are not guaranteed.",
      );
    } else {
      sentences.push("Review the proposed planning change before applying it.");
    }
  }
  return [
    ...Array.from(
      overtime.values(),
      (group) =>
        `${group.name}: ${group.hours} extra hours across ${group.shifts.length} shift${group.shifts.length === 1 ? "" : "s"}. ${group.shifts.join("; ")}.`,
    ),
    ...sentences,
  ];
}
