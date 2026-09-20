import { formatDateTime, humanize } from "../lib/format";

const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}T/;

type ActionRecord = Record<string, unknown>;

/**
 * Expand frozen simulation actions into labelled parameter rows.
 * IDs stay visible; names are shown alongside when the result already resolved them.
 */
export function ActionParameterList({
  actions,
  references = [],
}: {
  actions: ActionRecord[];
  references?: Array<{ id: string; name?: string }>;
}) {
  const names = collectNames(actions, references);
  return (
    <ol className="space-y-3">
      {actions.map((action, index) => {
        const type = typeof action.type === "string" ? action.type : "action";
        const rows = parameterRows(action, names);
        return (
          <li
            key={`${type}-${index}`}
            className="space-y-3 rounded-xl border border-border bg-muted/20 px-3 py-3"
          >
            <p className="text-sm font-medium">{humanize(type)}</p>
            {rows.length === 0 ? (
              <p className="text-sm text-muted-foreground">No additional parameters.</p>
            ) : (
              <dl className="space-y-2">
                {rows.map((row, rowIndex) => (
                  <div
                    key={`${row.label}-${rowIndex}`}
                    className="grid gap-1 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)] sm:gap-3"
                  >
                    <dt className="text-xs text-muted-foreground">{row.label}</dt>
                    <dd className="break-words text-sm">{row.value}</dd>
                  </div>
                ))}
              </dl>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function collectNames(
  actions: ActionRecord[],
  references: Array<{ id: string; name?: string }>,
): Map<string, string> {
  const names = new Map<string, string>();
  for (const item of references) {
    if (item.name) names.set(item.id, item.name);
  }
  for (const action of actions) {
    for (const [key, value] of Object.entries(action)) {
      if (!key.endsWith("Name") || typeof value !== "string") continue;
      const id = action[`${key.slice(0, -4)}Id`];
      if (typeof id === "string") names.set(id, value);
    }
    zipNamedOrder(action, "projectOrder", "projectOrderNames", names);
    zipNamedOrder(action, "taskOrder", "taskOrderNames", names);
  }
  return names;
}

function zipNamedOrder(
  action: ActionRecord,
  idsKey: string,
  namesKey: string,
  names: Map<string, string>,
): void {
  const ids = Array.isArray(action[idsKey]) ? action[idsKey] : [];
  const labels = Array.isArray(action[namesKey]) ? action[namesKey] : [];
  ids.forEach((id, index) => {
    const label = labels[index];
    if (typeof id === "string" && typeof label === "string") names.set(id, label);
  });
}

function parameterRows(
  action: ActionRecord,
  names: Map<string, string>,
): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [];
  const visit = (value: unknown, path: string) => {
    if (Array.isArray(value)) {
      if (!value.length) {
        rows.push({ label: path, value: "None" });
        return;
      }
      value.forEach((item, index) => visit(item, `${path} ${index + 1}`));
      return;
    }
    if (value && typeof value === "object") {
      Object.entries(value as ActionRecord).forEach(([key, item]) =>
        visit(item, `${path} · ${humanize(key)}`),
      );
      return;
    }
    rows.push({ label: path, value: displayValue(value, names) });
  };

  Object.entries(action).forEach(([key, value]) => {
    if (key === "type") return;
    if (key.endsWith("Names")) {
      const base = key.slice(0, -5);
      if (base in action || `${base}Ids` in action) return;
    }
    if (key.endsWith("Name") && `${key.slice(0, -4)}Id` in action) return;
    visit(value, humanize(key));
  });
  return rows;
}

function displayValue(value: unknown, names: Map<string, string>): string {
  if (value === null || value === undefined || value === "") return "Not supplied";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
  }
  if (typeof value !== "string") return String(value);
  if (ISO_DATETIME.test(value)) return formatDateTime(value);
  const name = names.get(value);
  return name && name !== value ? `${name} (${value})` : value;
}
