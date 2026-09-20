import { useState, type ChangeEvent, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, FileSpreadsheet, UploadCloud } from "lucide-react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorPanel, Skeleton } from "../components/ui";
import { Input } from "../components/ui/input";
import { Checkbox } from "../components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "../components/ui/dialog";
import { formBody, getErrorMessage, jsonBody, request, scopedPath } from "../lib/api";
import { formatAssumptionValue, formatDateTime, humanize } from "../lib/format";
import { queryKeys } from "../lib/query-keys";
import { useOnlineStatus } from "../lib/hooks";
import type {
  ImportBatchResponse,
  ImportIssueValue,
  ImportPreviewResponse,
  Organization,
} from "../lib/types";

export function ImportsPage({ organization }: { organization: Organization }) {
  const { importId } = useParams();
  const [searchParams] = useSearchParams();
  const projectId = searchParams.get("projectId") || undefined;
  return importId ? (
    <ImportDetail organization={organization} importId={importId} projectId={projectId} />
  ) : (
    <ImportUpload organization={organization} projectId={projectId} />
  );
}

function ImportUpload({
  organization,
  projectId,
}: {
  organization: Organization;
  projectId?: string;
}) {
  const navigate = useNavigate();
  const online = useOnlineStatus();
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string>();
  const preview = useMutation<ImportPreviewResponse, Error, File[]>({
    mutationFn: async (selectedFiles) => {
      const body = new FormData();
      selectedFiles.forEach((file) => body.append("files", file, file.name));
      return request<ImportPreviewResponse>(
        scopedPath(organization.id, "/imports/preview"),
        formBody(body),
      );
    },
    onSuccess: (result) => navigate(`/imports/${encodeURIComponent(result.id)}`),
    onError: (cause) =>
      setError(getErrorMessage(cause, "The portfolio files could not be previewed.")),
  });

  function selectFiles(event: ChangeEvent<HTMLInputElement>) {
    setFiles(Array.from(event.target.files || []));
    setError(undefined);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (files.length === 0) {
      setError("Choose a CSV or XLSX file before previewing the import.");
      return;
    }
    preview.mutate(files);
  }

  return (
    <div className="flex flex-col gap-6">
      <BackLink projectId={projectId} />
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Import spreadsheet</h1>
        <p className="text-sm text-muted-foreground">
          Upload a workbook or CSV set. Rows are validated before you confirm anything.
        </p>
      </header>
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.6fr)]">
        <Card className="p-6 sm:p-8">
          <div className="flex items-start gap-4">
            <span className="flex size-11 items-center justify-center rounded-2xl bg-secondary">
              <UploadCloud className="size-5" />
            </span>
            <div>
              <h2 className="text-lg font-semibold">Import spreadsheet</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Preview first; nothing is committed until you confirm the validated rows.
              </p>
            </div>
          </div>
          {error && (
            <div className="mt-6">
              <ErrorPanel title="Preview not created" message={error} />
            </div>
          )}
          {!online && (
            <p className="mt-5 text-sm text-muted-foreground">
              Reconnect before previewing an import.
            </p>
          )}
          <form className="mt-6 space-y-5" onSubmit={submit}>
            <label className="flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border bg-muted/35 px-5 text-center transition hover:border-primary/50 hover:bg-primary/5">
              <FileSpreadsheet className="size-7 text-primary" />
              <span className="font-medium">Choose CSV or XLSX files</span>
              <span className="text-sm text-muted-foreground">
                Projects, Tasks, Workers, Skills, Assignments, and optional Planning.
              </span>
              <Input
                className="sr-only"
                type="file"
                accept=".csv,.xlsx"
                multiple
                onChange={selectFiles}
              />
            </label>
            {files.length > 0 && (
              <div className="rounded-xl border border-border/70 p-4 text-sm">
                <p className="font-medium">Selected files</p>
                <ul className="mt-2 space-y-1 text-muted-foreground">
                  {files.map((file) => (
                    <li key={`${file.name}-${file.size}`}>
                      {file.name} · {formatBytes(file.size)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <Button
              type="submit"
              className="w-full sm:w-auto"
              disabled={!online || preview.isPending}
            >
              {preview.isPending ? "Validating files…" : "Preview import"}
            </Button>
          </form>
        </Card>
        <Card className="p-6 sm:p-8">
          <h2 className="text-lg font-semibold">Before confirming</h2>
          <ul className="mt-5 space-y-3 text-sm leading-6 text-muted-foreground">
            <li>
              <strong className="text-foreground">Use stable keys.</strong> External keys
              link rows before Timecue ids exist.
            </li>
            <li>
              <strong className="text-foreground">Expect corrections.</strong> Unmatched
              members, invalid links, and cycles block commit.
            </li>
            <li>
              <strong className="text-foreground">Planning is separate.</strong> Estimated
              effort and dependencies are shown as IntelliQ assumptions.
            </li>
            <li>
              <strong className="text-foreground">No formulas.</strong> Macros, formulas,
              and external workbook links are rejected by the preview.
            </li>
          </ul>
          <div className="mt-6 border-t border-border/70 pt-5">
            <p className="text-sm font-medium">Download templates</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <a
                className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-sm font-medium hover:bg-muted"
                href={`${scopedPath(organization.id, "/imports/template?format=xlsx")}`}
              >
                <Download className="size-4" /> XLSX
              </a>
              <a
                className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-border px-3 text-sm font-medium hover:bg-muted"
                href={`${scopedPath(organization.id, "/imports/template?format=csv")}`}
              >
                <Download className="size-4" /> CSV set
              </a>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

function ImportDetail({
  organization,
  importId,
  projectId,
}: {
  organization: Organization;
  importId: string;
  projectId?: string;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const online = useOnlineStatus();
  const [commitOpen, setCommitOpen] = useState(false);
  const [confirmPlanning, setConfirmPlanning] = useState(false);
  const [expandedRow, setExpandedRow] = useState<string>();
  const detail = useQuery<ImportBatchResponse>({
    queryKey: queryKeys.imports(organization.id, importId),
    queryFn: () =>
      request<ImportBatchResponse>(
        scopedPath(organization.id, `/imports/${encodeURIComponent(importId)}`),
      ),
    staleTime: 15_000,
  });
  const commit = useMutation<ImportBatchResponse, Error, void>({
    mutationFn: () =>
      request<ImportBatchResponse>(
        scopedPath(organization.id, `/imports/${encodeURIComponent(importId)}/commit`),
        jsonBody({
          previewHash: detail.data?.previewHash,
          confirmPlanning,
          resume: ["partially_applied", "needs_reconciliation"].includes(
            detail.data?.status || "",
          ),
        }),
      ),
    onSuccess: (result) => {
      setCommitOpen(false);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.imports(organization.id, importId),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.overview(organization.id),
      });
      void queryClient.invalidateQueries({ queryKey: ["planning", organization.id] });
    },
  });
  if (detail.isLoading) return <Skeleton className="min-h-[600px] rounded-3xl" />;
  if (detail.error)
    return (
      <div className="space-y-5">
        <BackLink projectId={projectId} />
        <ErrorPanel
          title="Import unavailable"
          message={getErrorMessage(detail.error, "The import batch could not be loaded.")}
          onRetry={() => void detail.refetch()}
        />
      </div>
    );
  const batch = detail.data;
  if (!batch)
    return (
      <EmptyState
        title="Import not found"
        description="This import batch is not available in the current organization."
      />
    );
  const errors = batch.errors || [];
  const warnings = batch.warnings || [];
  const canCommit =
    errors.length === 0 &&
    !["applied", "complete", "completed", "committed"].includes(batch.status);
  return (
    <div className="flex flex-col gap-6">
      <BackLink projectId={projectId} />
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Review import</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Batch {batch.id} · {formatDateTime(batch.updatedAt || batch.createdAt)}
          </p>
        </div>
        <Badge
          tone={
            errors.length
              ? "danger"
              : ["applied", "completed", "complete"].includes(batch.status)
                ? "success"
                : "warning"
          }
        >
          {humanize(batch.status)}
        </Badge>
      </header>
      {commit.error && (
        <ErrorPanel
          title="Import not committed"
          message={getErrorMessage(commit.error, "The import could not be committed.")}
        />
      )}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.6fr)]">
        <Card className="overflow-hidden p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-6">
            <div>
              <h2 className="font-display text-2xl font-medium">Validation rows</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {batch.rows.length} rows returned by the preview.
              </p>
            </div>
            <Button
              onClick={() => setCommitOpen(true)}
              disabled={!canCommit || !online || commit.isPending}
            >
              {commit.isPending
                ? "Committing…"
                : ["partially_applied", "needs_reconciliation"].includes(batch.status)
                  ? "Review and resume"
                  : "Confirm import"}
            </Button>
          </div>
          {batch.rows.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px] text-left text-sm">
                <thead className="bg-muted/45 text-xs uppercase tracking-[0.12em] text-muted-foreground">
                  <tr>
                    <th className="px-5 py-3">Sheet</th>
                    <th className="px-5 py-3">Row</th>
                    <th className="px-5 py-3">Entity</th>
                    <th className="px-5 py-3">External key</th>
                    <th className="px-5 py-3">Status</th>
                    <th className="px-5 py-3">Message</th>
                  </tr>
                </thead>
                <tbody>
                  {batch.rows.slice(0, 500).map((row, index) => (
                    <tr
                      key={
                        row.rowKey || row.id || `${row.sheet}-${row.rowNumber}-${index}`
                      }
                      className="border-t border-border/70"
                    >
                      <td className="px-5 py-3">{row.sheet || "—"}</td>
                      <td className="px-5 py-3">
                        {row.sourceRow || row.rowNumber || "—"}
                      </td>
                      <td className="px-5 py-3">
                        {humanize(row.entity || row.entityType) || "—"}
                      </td>
                      <td className="px-5 py-3">
                        {String(row.values?.externalKey || row.externalKey || "—")}
                      </td>
                      <td className="px-5 py-3">
                        <Badge
                          tone={
                            row.status === "error" || row.status === "invalid"
                              ? "danger"
                              : row.status === "warning"
                                ? "warning"
                                : "neutral"
                          }
                        >
                          {humanize(row.status)}
                        </Badge>
                      </td>
                      <td className="max-w-64 px-5 py-3 text-muted-foreground">
                        {row.errors?.join("; ") ||
                          getImportIssueMessage(row.message) ||
                          "—"}
                        {row.values && (
                          <>
                            <Button
                              variant="quiet"
                              size="sm"
                              onClick={() =>
                                setExpandedRow(
                                  expandedRow === String(index)
                                    ? undefined
                                    : String(index),
                                )
                              }
                            >
                              View fields
                            </Button>
                            {expandedRow === String(index) && (
                              <dl className="mt-2 space-y-2 text-xs">
                                {Object.entries(row.values)
                                  .filter(([, value]) => value !== null && value !== "")
                                  .map(([key, value]) => (
                                    <div key={key}>
                                      <dt className="font-medium">{humanize(key)}</dt>
                                      <dd className="break-words">
                                        {formatAssumptionValue(value)}
                                      </dd>
                                    </div>
                                  ))}
                              </dl>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="p-8">
              <EmptyState
                title="No rows returned"
                description="The parser did not return any import rows."
              />
            </div>
          )}
        </Card>
        <div className="space-y-6">
          <Card className="p-6">
            <h2 className="text-lg font-semibold">Counts</h2>
            <div className="mt-5 grid grid-cols-2 gap-4">
              {Object.entries(batch.counts)
                .filter(([, value]) => typeof value === "number")
                .map(([label, value]) => (
                  <div key={label} className="rounded-xl bg-muted/55 p-4">
                    <p className="text-xs text-muted-foreground">{humanize(label)}</p>
                    <p className="mt-2 text-2xl font-medium">{value}</p>
                  </div>
                ))}
            </div>
          </Card>
          {errors.length > 0 && (
            <Card className="border-destructive/30 p-6">
              <h2 className="text-lg font-semibold text-destructive">
                Corrections required
              </h2>
              <ul className="mt-4 space-y-3 text-sm leading-6 text-destructive/90">
                {errors.slice(0, 12).map((error, index) => (
                  <ImportIssueItem key={importIssueKey(error, index)} issue={error} />
                ))}
              </ul>
            </Card>
          )}
          {warnings.length > 0 && (
            <Card className="border-amber-300/70 bg-amber-50/60 p-6">
              <h2 className="text-lg font-semibold text-amber-800">Warnings</h2>
              <ul className="mt-4 space-y-3 text-sm leading-6 text-amber-900/80">
                {warnings.map((warning, index) => (
                  <ImportIssueItem key={importIssueKey(warning, index)} issue={warning} />
                ))}
              </ul>
            </Card>
          )}
          {batch.assumptions && batch.assumptions.length > 0 && (
            <Card className="p-6">
              <h2 className="text-lg font-semibold">Proposed assumptions</h2>
              <div className="mt-4 space-y-3">
                {batch.assumptions.map((assumption, index) => (
                  <div
                    key={assumption.id || index}
                    className="rounded-xl border border-border/70 p-3 text-sm"
                  >
                    <strong>{assumption.label || "Planning assumption"}</strong>
                    <p className="mt-1 text-muted-foreground">
                      {formatAssumptionValue(assumption.value ?? assumption.detail)}
                    </p>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      </div>
      {batch.receipts && batch.receipts.length > 0 && (
        <Card className="space-y-5 p-6">
          <h2 className="text-xl font-semibold">Import receipts</h2>
          <p className="text-sm text-muted-foreground">
            {batch.message} Planning: {humanize(batch.planningStatus || "none")}.
          </p>
          <ul className="divide-y divide-border">
            {batch.receipts.map((receipt, index) => (
              <li key={receipt.id || index} className="py-3">
                <div className="flex flex-wrap justify-between gap-2">
                  <strong className="text-sm">
                    {receipt.rowKey || receipt.rowId || "Import operation"}
                  </strong>
                  <Badge>{humanize(receipt.status)}</Badge>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">
                  {receipt.message || receipt.detail}
                </p>
                {receipt.upstreamId && (
                  <p className="mt-1 break-all text-xs">
                    Timecue record: {receipt.upstreamId}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Dialog open={commitOpen} onOpenChange={setCommitOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Confirm this import?</DialogTitle>
            <DialogDescription>
              Apply validated rows through existing Timecue APIs. Verified receipts are
              retained; unresolved operations stay explicit. Review source values before
              confirming planning inputs.
            </DialogDescription>
          </DialogHeader>
          {commit.error && (
            <ErrorPanel
              title="Import not committed"
              message={getErrorMessage(
                commit.error,
                "The import could not be committed.",
              )}
            />
          )}
          {batch.planningStatus === "proposed" && (
            <label className="flex items-start gap-3 rounded-xl border p-4 text-sm">
              <Checkbox
                checked={confirmPlanning}
                onCheckedChange={(value) => setConfirmPlanning(value === true)}
              />
              <span>
                Also confirm included effort, dependency, skill and transfer assumptions
                in IntelliQ. These stay separate from Timecue operational records.
              </span>
            </label>
          )}
          <DialogFooter>
            <Button variant="secondary" onClick={() => setCommitOpen(false)}>
              Keep reviewing
            </Button>
            <Button
              onClick={() => commit.mutate()}
              disabled={!online || commit.isPending}
            >
              {commit.isPending ? "Confirming…" : "Confirm import"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function BackLink({ projectId }: { projectId?: string }) {
  const destination = projectId
    ? `/projects/${encodeURIComponent(projectId)}`
    : "/projects";
  return (
    <Link
      to={destination}
      className="inline-flex min-h-11 items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" />
      {projectId ? "Back to project" : "Back to projects"}
    </Link>
  );
}

function ImportIssueItem({ issue }: { issue: ImportIssueValue }) {
  const message = getImportIssueMessage(issue);
  if (typeof issue === "string") return <li>{message}</li>;

  const context = [
    issue.sourceFile && `File ${issue.sourceFile}`,
    issue.sourceRow !== undefined && `Source row ${issue.sourceRow}`,
    issue.entity && humanize(issue.entity),
    issue.rowKey && `Row key ${issue.rowKey}`,
  ].filter(Boolean);

  return (
    <li>
      <p>{message}</p>
      {context.length > 0 && (
        <p className="mt-1 text-xs text-current/70">{context.join(" · ")}</p>
      )}
    </li>
  );
}

function getImportIssueMessage(issue?: ImportIssueValue | null): string {
  if (!issue) return "";
  if (typeof issue === "string") return issue;
  return issue.message || issue.detail || issue.code || "Import validation issue";
}

function importIssueKey(issue: ImportIssueValue, index: number): string {
  if (typeof issue === "string") return `${index}-${issue}`;
  return [issue.code, issue.rowKey, issue.sourceRow, index]
    .filter((value) => value !== undefined && value !== "")
    .join("-");
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
