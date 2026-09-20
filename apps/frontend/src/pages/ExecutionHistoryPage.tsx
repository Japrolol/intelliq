import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { Badge, ErrorPanel, Skeleton } from "../components/ui";
import { Button } from "../components/ui/button";
import { ExecutionReceipt } from "./DecisionReviewPage";
import { request, scopedPath, getErrorMessage } from "../lib/api";
import { describeActions } from "../lib/action-summary";
import { formatDateTime, humanize } from "../lib/format";
import type { ExecutionResponse, Organization } from "../lib/types";

export function ExecutionHistoryPage({ organization }: { organization: Organization }) {
  const { executionId } = useParams();
  const client = useQueryClient();
  const key = ["executions", organization.id];
  const { data, error, isLoading, refetch } = useQuery<ExecutionResponse[]>({
    queryKey: key,
    queryFn: () => request(scopedPath(organization.id, "/executions")),
    refetchInterval: 30_000,
  });
  const selected = data?.find((execution) => execution.id === executionId);
  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <Button asChild variant="ghost" size="sm">
          <Link to={executionId ? "/decisions" : "/overview"}>
            <ArrowLeft className="size-4" /> Back to decisions
          </Link>
        </Button>
        <h1 className="font-display text-2xl">
          {executionId ? "Decision details" : "Decision history"}
        </h1>
      </div>
      {isLoading && <Skeleton className="h-48" />}
      {error && (
        <ErrorPanel
          title="Could not load decisions"
          message={getErrorMessage(error)}
          onRetry={() => void refetch()}
        />
      )}
      {data && executionId && !selected && (
        <p className="text-muted-foreground">
          This decision is not available in this organization.
        </p>
      )}
      {selected && (
        <>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {formatDateTime(selected.approvedAt || selected.createdAt)}
            </p>
            {selected.analysisId && (
              <Button asChild variant="secondary">
                <Link to={`/analyses/${encodeURIComponent(selected.analysisId)}`}>
                  View simulation
                </Link>
              </Button>
            )}
          </div>
          <div className="divide-y rounded-xl border bg-card px-6 sm:px-8">
            {selected.steps.map((step) => (
              <div key={step.id} className="space-y-2 py-6">
                <div className="flex flex-wrap justify-between gap-3">
                  <h2 className="text-sm font-medium">
                    {step.title || "Planned change"}
                  </h2>
                  <Badge
                    tone={
                      step.status === "confirmed" || step.status === "applied"
                        ? "success"
                        : "neutral"
                    }
                  >
                    {humanize(step.status)}
                  </Badge>
                </div>
                <p className="text-sm leading-6 text-muted-foreground">
                  {step.action ? describeActions([step.action]).join(" ") : step.detail}
                </p>
              </div>
            ))}
          </div>
          <ExecutionReceipt
            key={selected.id}
            execution={selected}
            organization={organization}
            analysisId={selected.analysisId || ""}
            onExecutionChange={(updated) =>
              client.setQueryData<ExecutionResponse[]>(key, (rows) =>
                rows?.map((row) => (row.id === updated.id ? updated : row)),
              )
            }
          />
        </>
      )}
      {data &&
        !executionId &&
        (data.length === 0 ? (
          <p className="text-muted-foreground">
            No decisions have been applied or confirmed yet.
          </p>
        ) : (
          <div className="divide-y rounded-xl border bg-card">
            {data.map((execution) => {
              const pending = execution.steps.filter(
                (step) =>
                  !["applied", "confirmed", "cancelled"].includes(step.status || ""),
              ).length;
              return (
                <Link
                  key={execution.id}
                  to={`/executions/${encodeURIComponent(execution.id)}`}
                  className="flex items-center justify-between gap-4 p-6 transition-colors hover:bg-accent/40 sm:p-8"
                >
                  <div className="min-w-0 space-y-2">
                    <p className="font-medium">
                      {execution.steps
                        .map((step) => step.title)
                        .filter(Boolean)
                        .join(" · ") || "Approved plan"}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      {formatDateTime(execution.approvedAt || execution.createdAt)} ·{" "}
                      {pending ? `${pending} open steps` : humanize(execution.status)}
                    </p>
                  </div>
                  <ArrowRight className="size-4 shrink-0" />
                </Link>
              );
            })}
          </div>
        ))}
    </div>
  );
}
