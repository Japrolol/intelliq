import { SelectField, Choice } from "../components/SelectField";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorPanel,
  Skeleton,
  SourceBadge,
} from "../components/ui";
import { BackToWorkspace } from "../components/BackToWorkspace";
import { getErrorMessage, jsonBody, request, scopedPath } from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { useApiResource } from "../lib/hooks";
import type {
  DataMode,
  Organization,
  PortfolioResponse,
  Signal,
  SignalsResponse,
} from "../lib/types";

export function EvidencePage({
  organization,
  sourceMode,
}: {
  organization: Organization;
  sourceMode?: DataMode;
}) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const projectId = params.get("project") || "";
  const portfolio = useApiResource<PortfolioResponse>(
    scopedPath(organization.id, "/portfolio"),
  );
  const resolvedProjectId = projectId || portfolio.data?.projects[0]?.id || "";
  const path = resolvedProjectId
    ? scopedPath(
        organization.id,
        `/projects/${encodeURIComponent(resolvedProjectId)}/signals`,
      )
    : null;
  const { data, error, isLoading, mutate } = useApiResource<SignalsResponse>(path);
  const [activeSignalId, setActiveSignalId] = useState<string>();
  const [reviewError, setReviewError] = useState<string>();
  const [isReviewing, setIsReviewing] = useState(false);

  async function reviewSignal(signal: Signal, status: "confirmed" | "rejected") {
    setActiveSignalId(signal.id);
    setReviewError(undefined);
    setIsReviewing(true);
    try {
      await request(
        scopedPath(organization.id, `/signals/${encodeURIComponent(signal.id)}/review`),
        jsonBody({ status }),
      );
      await mutate();
    } catch (requestError) {
      setReviewError(
        getErrorMessage(requestError, "The evidence review could not be saved."),
      );
    } finally {
      setActiveSignalId(undefined);
      setIsReviewing(false);
    }
  }

  const rawSignals = data?.signals || [
    ...(data?.reviewed || []),
    ...(data?.pending || []),
  ];
  const signals = rawSignals.map(normalizeSignal);
  const pendingSignals = signals.filter(
    (signal) => signal.reviewStatus === "pending" || signal.reviewStatus === "proposed",
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <BackToWorkspace />
        <h1>Evidence</h1>
      </div>
      {error && (
        <ErrorPanel
          title="Evidence unavailable"
          message={getErrorMessage(error, "Signals could not be loaded.")}
          onRetry={() => mutate()}
        />
      )}
      {portfolio.error && (
        <ErrorPanel
          title="Projects unavailable"
          message={getErrorMessage(portfolio.error, "Projects could not be loaded.")}
          onRetry={() => portfolio.mutate()}
        />
      )}
      {reviewError && <ErrorPanel title="Review not saved" message={reviewError} />}
      {isLoading && <Skeleton className="evidence-skeleton" />}
      {data && (
        <>
          {portfolio.data && (
            <label className="flex min-w-0 flex-col gap-2 text-sm font-medium">
              <span>Project</span>
              <SelectField
                value={resolvedProjectId}
                onValueChange={(value) =>
                  navigate(`/evidence?project=${encodeURIComponent(value)}`)
                }
              >
                <Choice value="" disabled>
                  Select a project
                </Choice>
                {portfolio.data.projects.map((project) => (
                  <Choice key={project.id} value={project.id}>
                    {project.name}
                  </Choice>
                ))}
              </SelectField>
            </label>
          )}
          <div className="flex flex-wrap items-center gap-3 text-muted-foreground">
            <SourceBadge mode={data.sourceMode || sourceMode} />
            <span>{pendingSignals.length} pending</span>
          </div>
          <Card>
            <div className="flex flex-wrap items-center gap-3">
              <h2>Signals</h2>
              <span className="text-muted-foreground">{signals.length}</span>
            </div>
            <div className="flex flex-col gap-6">
              {signals.length === 0 ? (
                <EmptyState
                  icon="file-text"
                  title="No signals returned"
                  description="No reviewable signals were returned for this project."
                />
              ) : (
                signals.map((signal) => (
                  <SignalRow
                    key={signal.id}
                    signal={signal}
                    active={activeSignalId === signal.id}
                    onConfirm={() => reviewSignal(signal, "confirmed")}
                    onReject={() => reviewSignal(signal, "rejected")}
                    isReviewing={isReviewing}
                  />
                ))
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function SignalRow({
  signal,
  active,
  onConfirm,
  onReject,
  isReviewing,
}: {
  signal: Signal;
  active: boolean;
  onConfirm: () => void;
  onReject: () => void;
  isReviewing: boolean;
}) {
  const isPending =
    signal.reviewStatus === "pending" || signal.reviewStatus === "proposed";
  return (
    <article className="flex flex-col gap-3 border-t py-5">
      <div className="flex flex-wrap items-center gap-3">
        <Badge
          tone={
            signal.reviewStatus === "confirmed"
              ? "success"
              : signal.reviewStatus === "rejected"
                ? "danger"
                : "warning"
          }
        >
          {humanize(signal.reviewStatus)}
        </Badge>
        <span className="text-muted-foreground">
          {signal.sourceName || signal.source?.sourceKind || "Source document"} ·{" "}
          {formatDateTime(signal.sourceDate)}
        </span>
      </div>
      <h3>{signal.title || "Reported operational signal"}</h3>
      <blockquote>“{signal.quote || "No exact quote was returned."}”</blockquote>
      <div className="flex flex-wrap items-center gap-3 text-muted-foreground">
        <span>
          <strong>Assertion</strong> {humanize(signal.assertion)}
        </span>
        <span>
          <strong>State</strong> {humanize(signal.state)}
        </span>
        {signal.sourceRevision && (
          <span>
            <strong>Revision</strong> {signal.sourceRevision}
          </span>
        )}
      </div>
      {signal.planningChange && (
        <p>
          <strong>Suggested planning change:</strong> {signal.planningChange}
        </p>
      )}
      {isPending && (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" size="sm" onClick={onReject} disabled={active}>
            {active && isReviewing ? "Saving…" : "Keep outside model"}
          </Button>
          <Button size="sm" onClick={onConfirm} disabled={active}>
            {active && isReviewing ? "Saving…" : "Confirm evidence"}
          </Button>
        </div>
      )}
    </article>
  );
}

function normalizeSignal(signal: Signal): Signal {
  const observation = signal.observation;
  const status =
    signal.reviewStatus || signal.status || observation?.reviewStatus || "pending";
  return {
    ...signal,
    title: signal.title || observation?.kind,
    quote:
      signal.quote ||
      signal.evidenceQuotes?.[0]?.text ||
      observation?.evidenceQuotes?.[0]?.text,
    assertion: signal.assertion || observation?.assertion,
    state: signal.state || observation?.state,
    sourceRevision: signal.sourceRevision || signal.source?.sourceRevision,
    planningChange: signal.planningChange || observation?.planningChange,
    reviewStatus: status,
  };
}
