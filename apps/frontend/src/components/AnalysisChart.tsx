import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card } from "./ui";
import { humanize } from "../lib/format";
import type { AnalysisResponse } from "../lib/types";

export function RiskChart({
  data,
}: {
  data: Array<{ name: string; risk: number | null }>;
}) {
  if (!data.some((item) => item.risk !== null))
    return (
      <p className="flex min-h-60 items-center justify-center text-sm text-muted-foreground">
        Run an analysis to see the forecast.
      </p>
    );
  return (
    <div
      className="min-w-0"
      role="img"
      aria-label="Modeled probability of delay, in percent"
    >
      <ResponsiveContainer width="100%" height={240}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 12, right: 12, left: 0, bottom: 8 }}
        >
          <CartesianGrid
            horizontal={false}
            stroke="var(--border)"
            strokeDasharray="3 5"
          />
          <YAxis
            type="category"
            width={100}
            dataKey="name"
            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
            tickLine={false}
            axisLine={false}
            interval={0}
            tickFormatter={(value) => String(value).split(" · ")[0].slice(0, 17)}
          />
          <XAxis
            type="number"
            domain={[0, 100]}
            tickFormatter={(value) => `${value}%`}
            tick={{ fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            formatter={(value) => [`${value}%`, "Delay risk"]}
            contentStyle={{
              borderRadius: 10,
              borderColor: "var(--border)",
              background: "var(--card)",
            }}
          />
          <Bar
            dataKey="risk"
            fill="var(--chart-1)"
            radius={[0, 5, 5, 0]}
            maxBarSize={48}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
        {data.map((item) => (
          <span key={item.name}>
            {item.name}:{" "}
            <strong>{item.risk === null ? "Not available" : `${item.risk}%`}</strong>
          </span>
        ))}
      </div>
    </div>
  );
}

export function AnalysisChart({ analysis }: { analysis: AnalysisResponse }) {
  const baseline = analysis.baselineByProject?.[analysis.projectId];
  const data = [
    { name: "Current plan", risk: percent(baseline?.delayProbability) },
    ...(analysis.strategies || [])
      .filter((s) => s.id !== "baseline" && s.feasible !== false)
      .map((s) => ({
        name: humanize(s.label || s.name || "Recovery"),
        risk: percent(s.target?.delayProbability),
      })),
  ];
  return (
    <Card>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-4">
        <h2>What changes the risk?</h2>
        <small>{analysis.sampleCount ?? "—"} simulations · target project</small>
      </div>
      <RiskChart data={data} />
    </Card>
  );
}
export function percent(value?: number | null): number | null {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.round(value * 100)
    : null;
}
