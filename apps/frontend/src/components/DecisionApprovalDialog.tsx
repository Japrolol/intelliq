import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ReviewPreview } from "../pages/DecisionReviewPage";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { ErrorPanel, Skeleton } from "./ui";
import { request, scopedPath, jsonBody, getErrorMessage } from "../lib/api";
import { queryKeys } from "../lib/query-keys";
import { useOnlineStatus } from "../lib/hooks";
import type { ReviewResponse, ExecutionResponse } from "../lib/types";

export function DecisionApprovalDialog({
  organizationId,
  analysisId,
  strategyId,
  onClose,
}: {
  organizationId: string;
  analysisId: string;
  strategyId: string;
  onClose: () => void;
}) {
  const online = useOnlineStatus();
  const client = useQueryClient();
  const keys = useRef(new Map<string, string>());
  const review = useQuery({
    queryKey: ["decision-preview", organizationId, analysisId, strategyId],
    queryFn: () =>
      request<ReviewResponse>(
        scopedPath(organizationId, `/analyses/${encodeURIComponent(analysisId)}/review`),
        jsonBody({ strategyId }),
      ),
    enabled: online,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
  const apply = useMutation({
    mutationFn: (preview: ReviewResponse) => {
      if (!keys.current.has(preview.reviewHash))
        keys.current.set(preview.reviewHash, crypto.randomUUID());
      return request<ExecutionResponse>(
        scopedPath(organizationId, `/analyses/${encodeURIComponent(analysisId)}/apply`),
        jsonBody({
          strategyId: preview.strategyId,
          reviewHash: preview.reviewHash,
          idempotencyKey: keys.current.get(preview.reviewHash),
        }),
      );
    },
    onSuccess: () =>
      client.invalidateQueries({ queryKey: queryKeys.overview(organizationId) }),
  });
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !apply.isPending) onClose();
      }}
    >
      <DialogContent className="max-h-[90dvh] gap-6 overflow-y-auto p-6 sm:max-w-3xl sm:gap-8 sm:p-8">
        <DialogHeader className="pr-8">
          <DialogTitle className="font-display text-2xl">Review changes</DialogTitle>
        </DialogHeader>
        {!online && <p>Connect to the internet to review and apply changes.</p>}
        {review.isLoading && <Skeleton className="h-32" />}
        {review.error && (
          <ErrorPanel
            title="Preview unavailable"
            message={getErrorMessage(review.error)}
          />
        )}
        {apply.error && (
          <ErrorPanel
            title="Could not apply changes"
            message={getErrorMessage(apply.error)}
          />
        )}
        {apply.data ? (
          <div className="space-y-6 py-2 text-sm leading-6">
            <p>
              {apply.data.status === "applied"
                ? "Changes saved and verified in Timecue."
                : apply.data.steps.every((step) => step.kind === "manual")
                  ? "Plan recorded. The on-site actions are still pending; no Timecue records were changed."
                  : "Review the saved changes and remaining steps below. Some actions still need attention."}
            </p>
            <ul className="space-y-2">
              {apply.data.steps.map((step) => (
                <li
                  key={step.id}
                  className="flex flex-wrap justify-between gap-4 rounded-lg border p-5"
                >
                  <span>{step.title || "Planned change"}</span>
                  <span className="text-muted-foreground">
                    {step.status === "applied"
                      ? "Saved in Timecue"
                      : step.status === "confirmed"
                        ? "Confirmed"
                        : step.status === "pending"
                          ? "Pending"
                          : "Needs attention"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          review.data && (
            <ReviewPreview
              review={review.data}
              applyPending={apply.isPending}
              onApply={() => apply.mutate(review.data!)}
              disabled={!online || review.isFetching || Boolean(review.error)}
              onClose={onClose}
            />
          )
        )}
      </DialogContent>
    </Dialog>
  );
}
