import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileUp, Search, Trash2, X } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { ProjectKnowledgeGraph } from "../components/ProjectKnowledgeGraph";
import { Choice, SelectField } from "../components/SelectField";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "../components/ui/sheet";
import { Skeleton } from "../components/ui";
import { formBody, getErrorMessage, request, scopedPath } from "../lib/api";
import { formatDateTime, humanize } from "../lib/format";
import { useOnlineStatus } from "../lib/hooks";
import { queryKeys } from "../lib/query-keys";
import type {
  EvidenceClaim,
  JobResponse,
  KnowledgeDocument,
  KnowledgeResponse,
  Organization,
  PortfolioResponse,
} from "../lib/types";
import { cn } from "../lib/utils";

const GENERAL_KNOWLEDGE_SCOPE = "__organization__";
const KNOWLEDGE_FACT_PREFIX = "knowledge:";
const MAX_SEARCH_FINDINGS = 5;
const EMPTY_KNOWLEDGE: KnowledgeResponseWithSemanticLinks = {
  documents: [],
  claims: [],
};

type SemanticLink = {
  source: string;
  target: string;
  similarity: number;
  kind: "related";
};

type KnowledgeDocumentWithProvenance = KnowledgeDocument & {
  provenance?: { sourceType?: string };
};

type KnowledgeResponseWithSemanticLinks = Omit<KnowledgeResponse, "documents"> & {
  documents: KnowledgeDocumentWithProvenance[];
  semanticLinks?: SemanticLink[];
};

type KnowledgeSelection =
  { kind: "document"; id: string } | { kind: "claim"; id: string };

function knowledgeSelectionFromParams(
  searchParams: URLSearchParams,
): KnowledgeSelection | undefined {
  const documentId = searchParams.get("documentId")?.trim();
  if (documentId) return { kind: "document", id: documentId };
  const claimId = searchParams.get("claimId")?.trim();
  if (!claimId) return undefined;
  const actualClaimId = claimId.startsWith(KNOWLEDGE_FACT_PREFIX)
    ? claimId.slice(KNOWLEDGE_FACT_PREFIX.length)
    : claimId;
  return actualClaimId ? { kind: "claim", id: actualClaimId } : undefined;
}

type KnowledgeUploadResponse = {
  jobId: string;
  id: string;
  status: string;
};

type SemanticSearchResponse = {
  status: "available" | "unavailable";
  results: Array<{ documentId: string; similarity: number }>;
};

type KnowledgeImportJob = JobResponse & {
  result?: NonNullable<JobResponse["result"]> & { documentId?: string };
};

type ImportJobState = {
  jobId: string;
  fileName: string;
  file?: File;
};

type DeleteResponse = { status: "deleted"; id: string };

function knowledgePath(organizationId: string, projectId?: string): string {
  const path = scopedPath(organizationId, "/knowledge");
  return projectId ? `${path}?projectId=${encodeURIComponent(projectId)}` : path;
}

function isAnalysisSummary(document: KnowledgeDocumentWithProvenance): boolean {
  return document.provenance?.sourceType === "analysis_summary";
}

function documentTitle(document: KnowledgeDocumentWithProvenance): string {
  return isAnalysisSummary(document)
    ? "Analysis"
    : document.name || document.fileName || document.id;
}

function documentName(document: KnowledgeDocumentWithProvenance): string {
  return document.name || document.fileName || document.id;
}

function documentState(document: KnowledgeDocumentWithProvenance): string | undefined {
  return document.extractionStatus || document.extraction?.status || document.status;
}

function findingsCountLabel(count: number): string {
  if (count === 0) return "No extracted findings";
  if (count === 1) return "1 finding";
  return `${count} findings`;
}

function claimLabel(claim: EvidenceClaim): string {
  return claim.summary || claim.assertion || claim.quote || "Finding";
}

function claimsForDocument(
  claims: ReadonlyArray<EvidenceClaim>,
  documentId: string,
): EvidenceClaim[] {
  return claims.filter((claim) => claimDocumentId(claim) === documentId);
}

function claimDocumentId(claim: EvidenceClaim): string | undefined {
  if (claim.sourceDocumentId) return claim.sourceDocumentId;
  if (claim.documentId) return claim.documentId;
  return typeof claim.source === "object" ? claim.source.documentId : undefined;
}

function claimSourceLabel(
  claim: EvidenceClaim,
  documents: ReadonlyMap<string, KnowledgeDocumentWithProvenance>,
): string {
  const sourceDocument = claimDocumentId(claim)
    ? documents.get(claimDocumentId(claim) || "")
    : undefined;
  if (sourceDocument) return documentTitle(sourceDocument);
  if (typeof claim.source === "object" && claim.source.fileName) {
    return claim.source.fileName;
  }
  if (typeof claim.source === "string" && claim.source.trim()) return claim.source;
  return "Source document";
}

function knowledgeState(value?: string): string | undefined {
  if (!value) return undefined;
  if (value === "confirmed" || value === "accepted") return "Accepted";
  if (value === "proposed") return "Imported";
  if (value === "rejected") return "Excluded";
  return humanize(value);
}

function isTerminalImportStatus(status?: string): boolean {
  return status === "completed" || status === "failed" || status === "expired";
}

function isFailedImportStatus(status?: string): boolean {
  return status === "failed" || status === "expired";
}

function jobErrorMessage(job?: KnowledgeImportJob): string | undefined {
  if (!job?.error) return undefined;
  if (typeof job.error === "string") return job.error;
  return job.error.message || "The import could not be completed.";
}

function deleteTargetLabel(
  target: KnowledgeSelection | undefined,
  documents: ReadonlyMap<string, KnowledgeDocumentWithProvenance>,
  claims: ReadonlyMap<string, EvidenceClaim>,
): string {
  if (!target) return "knowledge";
  if (target.kind === "document") {
    const document = documents.get(target.id);
    return document ? documentTitle(document) : "document";
  }
  const claim = claims.get(target.id);
  return claim?.summary || claim?.assertion || "claim";
}

export function KnowledgePage({
  organization,
  projectId,
  embedded = false,
}: {
  organization: Organization;
  projectId?: string;
  embedded?: boolean;
}) {
  const [searchParams] = useSearchParams();
  const linkedSelection = useMemo(
    () => knowledgeSelectionFromParams(searchParams),
    [searchParams],
  );
  const linkedSelectionKey = linkedSelection
    ? `${linkedSelection.kind}:${linkedSelection.id}`
    : undefined;
  const queryProjectId = searchParams.get("projectId") || undefined;
  const fixedProjectId = projectId || queryProjectId;
  const [selectedProjectId, setSelectedProjectId] = useState(
    fixedProjectId || GENERAL_KNOWLEDGE_SCOPE,
  );
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<SemanticSearchResponse["results"]>(
    [],
  );
  const [searchMessage, setSearchMessage] = useState<string>();
  const [uploadError, setUploadError] = useState<string>();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selection, setSelection] = useState<KnowledgeSelection>();
  const [linkedKnowledgeMessage, setLinkedKnowledgeMessage] = useState<string>();
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeSelection>();
  const [importJob, setImportJob] = useState<ImportJobState>();
  const [progressCollapsed, setProgressCollapsed] = useState(true);
  const completedImportRef = useRef<string>();
  const linkedTargetConsumedRef = useRef<string>();
  const online = useOnlineStatus();
  const queryClient = useQueryClient();
  const importStorageKey = `intelliq:knowledge-import:${organization.id}`;
  useEffect(() => {
    try {
      const jobId = sessionStorage.getItem(importStorageKey);
      setImportJob(jobId ? { jobId, fileName: "Document import" } : undefined);
    } catch {
      setImportJob(undefined);
    }
  }, [importStorageKey]);
  const activeProjectId =
    fixedProjectId ||
    (selectedProjectId !== GENERAL_KNOWLEDGE_SCOPE ? selectedProjectId : undefined);
  const portfolio = useQuery<PortfolioResponse>({
    queryKey: ["portfolio", organization.id],
    queryFn: () => request<PortfolioResponse>(scopedPath(organization.id, "/portfolio")),
    enabled: !fixedProjectId,
    staleTime: 30_000,
  });
  const knowledgeQueryKey = useMemo(
    () =>
      [
        ...queryKeys.knowledge(organization.id),
        activeProjectId || GENERAL_KNOWLEDGE_SCOPE,
      ] as const,
    [activeProjectId, organization.id],
  );
  const knowledge = useQuery<KnowledgeResponseWithSemanticLinks>({
    queryKey: knowledgeQueryKey,
    queryFn: () =>
      request<KnowledgeResponseWithSemanticLinks>(
        knowledgePath(organization.id, activeProjectId),
      ),
    staleTime: 15_000,
  });
  const importStatus = useQuery<KnowledgeImportJob>({
    queryKey: queryKeys.job(organization.id, importJob?.jobId || "knowledge-import"),
    queryFn: () =>
      request<KnowledgeImportJob>(
        scopedPath(
          organization.id,
          `/jobs/${encodeURIComponent(importJob?.jobId || "")}`,
        ),
      ),
    enabled: Boolean(importJob?.jobId),
    refetchOnReconnect: true,
    refetchInterval: (query) => {
      if (
        !importJob?.jobId ||
        !online ||
        isTerminalImportStatus(query.state.data?.status)
      ) {
        return false;
      }
      return 1_500;
    },
  });
  const data = knowledge.data || EMPTY_KNOWLEDGE;
  const documentsById = useMemo(
    () => new Map(data.documents.map((document) => [document.id, document])),
    [data.documents],
  );
  const claimsById = useMemo(
    () => new Map(data.claims.map((claim) => [claim.id, claim])),
    [data.claims],
  );
  const selectedDocument =
    selection?.kind === "document" ? documentsById.get(selection.id) : undefined;
  const selectedClaim =
    selection?.kind === "claim" ? claimsById.get(selection.id) : undefined;
  const graphDocumentId =
    selectedDocument?.id || (selectedClaim && claimDocumentId(selectedClaim));
  const deleteLabel = deleteTargetLabel(deleteTarget, documentsById, claimsById);
  const scopeLabel = activeProjectId
    ? portfolio.data?.projects.find((project) => project.id === activeProjectId)?.name ||
      (fixedProjectId ? "Project knowledge" : "Selected project")
    : "Organization knowledge";

  useEffect(() => {
    if (selection && !selectedDocument && !selectedClaim) setSelection(undefined);
  }, [selectedClaim, selectedDocument, selection]);

  useEffect(() => {
    if (
      !linkedSelection ||
      !linkedSelectionKey ||
      !knowledge.data ||
      knowledge.isFetching ||
      knowledge.error ||
      linkedTargetConsumedRef.current === linkedSelectionKey
    ) {
      return;
    }
    linkedTargetConsumedRef.current = linkedSelectionKey;
    const item =
      linkedSelection.kind === "document"
        ? documentsById.get(linkedSelection.id)
        : claimsById.get(linkedSelection.id);
    if (item) {
      setLinkedKnowledgeMessage(undefined);
      setSelection(linkedSelection);
      return;
    }
    setSelection(undefined);
    setLinkedKnowledgeMessage(
      linkedSelection.kind === "document"
        ? "That knowledge document is no longer available."
        : "That knowledge claim is no longer available.",
    );
  }, [
    claimsById,
    documentsById,
    knowledge.data,
    knowledge.error,
    knowledge.isFetching,
    linkedSelection,
    linkedSelectionKey,
  ]);

  useEffect(() => {
    if (!importJob || importStatus.data?.status !== "completed") return;
    if (completedImportRef.current === importJob.jobId) return;
    completedImportRef.current = importJob.jobId;
    void invalidateKnowledgeViews();
  }, [importJob, importStatus.data?.status]);

  async function invalidateKnowledgeViews(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.knowledge(organization.id) }),
      queryClient.invalidateQueries({ queryKey: knowledgeQueryKey }),
      queryClient.invalidateQueries({ queryKey: queryKeys.overview(organization.id) }),
    ]);
  }

  const upload = useMutation<KnowledgeUploadResponse, Error, File>({
    mutationFn: async (selectedFile) => {
      const body = new FormData();
      body.append("file", selectedFile, selectedFile.name);
      if (activeProjectId) body.append("projectId", activeProjectId);
      return request<KnowledgeUploadResponse>(
        scopedPath(organization.id, "/knowledge/documents"),
        formBody(body),
      );
    },
    onSuccess: (result, selectedFile) => {
      const jobId = result.jobId || result.id;
      setUploadError(undefined);
      setProgressCollapsed(true);
      completedImportRef.current = undefined;
      setImportJob({ jobId, fileName: selectedFile.name, file: selectedFile });
      try {
        // Keep only an opaque job reference, never file content or credentials.
        sessionStorage.setItem(importStorageKey, jobId);
      } catch {
        // Import still runs if browser session storage is unavailable.
      }
    },
    onError: (error) =>
      setUploadError(getErrorMessage(error, "The import could not be queued.")),
  });
  const deleteKnowledge = useMutation<DeleteResponse, Error, KnowledgeSelection>({
    mutationFn: (target) =>
      request<DeleteResponse>(
        scopedPath(
          organization.id,
          target.kind === "document"
            ? `/knowledge/documents/${encodeURIComponent(target.id)}`
            : `/knowledge/claims/${encodeURIComponent(target.id)}`,
        ),
        { method: "DELETE" },
      ),
    onSuccess: async () => {
      setDeleteTarget(undefined);
      setSelection(undefined);
      await invalidateKnowledgeViews();
    },
  });
  const semanticSearch = useMutation<SemanticSearchResponse, Error, string>({
    mutationFn: (query) =>
      request<SemanticSearchResponse>(scopedPath(organization.id, "/knowledge/search"), {
        method: "POST",
        body: JSON.stringify({
          query,
          ...(activeProjectId ? { projectId: activeProjectId } : {}),
        }),
      }),
  });

  function openImport() {
    if (!online || upload.isPending) return;
    upload.reset();
    setUploadError(undefined);
    fileInputRef.current?.click();
  }

  function selectFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    event.target.value = "";
    if (!selected) return;
    if (!online) {
      setUploadError("Reconnect before importing knowledge.");
      return;
    }
    setUploadError(undefined);
    upload.mutate(selected);
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = search.trim();
    if (!query) return;
    setSearchMessage(undefined);
    semanticSearch.mutate(query, {
      onSuccess: (result) => {
        if (result.status === "unavailable") {
          setSearchResults([]);
          setSearchMessage("Semantic search is unavailable.");
          return;
        }
        const availableResults = result.results.filter((candidate) =>
          documentsById.has(candidate.documentId),
        );
        if (availableResults.length > 0) {
          setSearchResults(availableResults);
          return;
        }
        setSearchResults([]);
        setSearchMessage("No matching documents.");
      },
      onError: (error) =>
        setSearchMessage(getErrorMessage(error, "Semantic search is unavailable.")),
    });
  }

  function openDelete(target: KnowledgeSelection) {
    deleteKnowledge.reset();
    setDeleteTarget(target);
  }

  function retryImport() {
    if (!importJob?.file || !online || upload.isPending) return;
    setUploadError(undefined);
    upload.mutate(importJob.file);
  }

  if (knowledge.isLoading) {
    return (
      <div className="space-y-6">
        {!embedded && <BackLink projectId={fixedProjectId} />}
        <Skeleton className="min-h-[620px] rounded-xl" />
      </div>
    );
  }

  if (knowledge.error) {
    return (
      <div className="space-y-6">
        {!embedded && <BackLink projectId={fixedProjectId} />}
        <p
          role="alert"
          className="rounded-xl border border-destructive/30 p-5 text-sm text-destructive"
        >
          {getErrorMessage(knowledge.error, "Knowledge could not be loaded.")}
        </p>
      </div>
    );
  }

  return (
    <div className={cn("space-y-6", embedded ? "pt-1" : "")}>
      {!embedded && <BackLink projectId={fixedProjectId} />}
      <h1 className="sr-only">{embedded ? "Project knowledge" : "Knowledge"}</h1>
      {linkedKnowledgeMessage && (
        <p role="alert" className="text-sm text-destructive">
          {linkedKnowledgeMessage}
        </p>
      )}
      <ProjectKnowledgeGraph
        documents={data.documents}
        claims={data.claims}
        semanticLinks={data.semanticLinks}
        scopeLabel={scopeLabel}
        toolbar={
          <KnowledgeToolbar
            showScope={!fixedProjectId}
            selectedProjectId={selectedProjectId}
            projects={portfolio.data?.projects || []}
            scopeLoading={portfolio.isLoading}
            scopeError={Boolean(portfolio.error)}
            onProjectChange={(value) => {
              setSelectedProjectId(value);
              setSelection(undefined);
              setLinkedKnowledgeMessage(undefined);
              setSearchResults([]);
              setSearchMessage(undefined);
            }}
            onRetryScope={() => void portfolio.refetch()}
            search={search}
            searchResults={searchResults}
            documents={documentsById}
            claims={data.claims}
            searchMessage={searchMessage}
            searchPending={semanticSearch.isPending}
            online={online}
            onSearchChange={(value) => {
              setSearch(value);
              if (!value.trim()) {
                setSearchResults([]);
                setSearchMessage(undefined);
              }
            }}
            onSubmitSearch={submitSearch}
            onSelectSearchResult={(target) => {
              setSelection(target);
              setLinkedKnowledgeMessage(undefined);
              setSearchResults([]);
              setSearchMessage(undefined);
            }}
            onImport={openImport}
            importPending={upload.isPending}
          />
        }
        selectedDocumentId={graphDocumentId}
        selectedClaimId={selection?.kind === "claim" ? selection.id : undefined}
        onSelectDocument={(id) => {
          setLinkedKnowledgeMessage(undefined);
          setSelection({ kind: "document", id });
        }}
        onSelectClaim={(id) => {
          setLinkedKnowledgeMessage(undefined);
          setSelection({ kind: "claim", id });
        }}
      />
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*,.pdf,.txt,.md,.doc,.docx"
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        disabled={!online || upload.isPending}
        onChange={selectFile}
      />
      {uploadError && (
        <p role="alert" className="text-sm text-destructive">
          {uploadError}
        </p>
      )}
      <KnowledgeDetailsSheet
        selection={selection}
        document={selectedDocument}
        claim={selectedClaim}
        documents={documentsById}
        findings={
          selectedDocument ? claimsForDocument(data.claims, selectedDocument.id) : []
        }
        online={online}
        pending={deleteKnowledge.isPending}
        onOpenChange={(open) => {
          if (!open) setSelection(undefined);
        }}
        onSelectFinding={(id) => setSelection({ kind: "claim", id })}
        onDelete={openDelete}
      />
      <DeleteKnowledgeDialog
        target={deleteTarget}
        label={deleteLabel}
        online={online}
        pending={deleteKnowledge.isPending}
        error={deleteKnowledge.error}
        onOpenChange={(open) => {
          if (!open && !deleteKnowledge.isPending) {
            setDeleteTarget(undefined);
            deleteKnowledge.reset();
          }
        }}
        onConfirm={() => {
          if (deleteTarget && online) deleteKnowledge.mutate(deleteTarget);
        }}
      />
      {importJob && (
        <ImportProgressCard
          job={importJob}
          status={importStatus.data}
          statusError={importStatus.error}
          collapsed={progressCollapsed}
          online={online}
          retryPending={upload.isPending}
          retryError={upload.error}
          onToggle={() => setProgressCollapsed((current) => !current)}
          onRetry={retryImport}
          onDismiss={() => {
            setImportJob(undefined);
            try {
              sessionStorage.removeItem(importStorageKey);
            } catch {
              /* Storage is optional. */
            }
          }}
        />
      )}
    </div>
  );
}

function KnowledgeToolbar({
  showScope,
  selectedProjectId,
  projects,
  scopeLoading,
  scopeError,
  onProjectChange,
  onRetryScope,
  search,
  searchResults,
  documents,
  claims,
  searchMessage,
  searchPending,
  online,
  onSearchChange,
  onSubmitSearch,
  onSelectSearchResult,
  onImport,
  importPending,
}: {
  showScope: boolean;
  selectedProjectId: string;
  projects: PortfolioResponse["projects"];
  scopeLoading: boolean;
  scopeError: boolean;
  onProjectChange: (value: string) => void;
  onRetryScope: () => void;
  search: string;
  searchResults: SemanticSearchResponse["results"];
  documents: ReadonlyMap<string, KnowledgeDocumentWithProvenance>;
  claims: EvidenceClaim[];
  searchMessage?: string;
  searchPending: boolean;
  online: boolean;
  onSearchChange: (value: string) => void;
  onSubmitSearch: (event: FormEvent<HTMLFormElement>) => void;
  onSelectSearchResult: (selection: KnowledgeSelection) => void;
  onImport: () => void;
  importPending: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
      {showScope && (
        <div className="w-full min-w-52 sm:w-56">
          <span className="sr-only">Knowledge scope</span>
          <SelectField
            value={selectedProjectId}
            onValueChange={onProjectChange}
            disabled={scopeLoading}
            ariaLabel="Knowledge scope"
          >
            <Choice value={GENERAL_KNOWLEDGE_SCOPE}>Organization knowledge</Choice>
            {projects.map((project) => (
              <Choice key={project.id} value={project.id}>
                {project.name}
              </Choice>
            ))}
          </SelectField>
          {scopeError && (
            <button
              type="button"
              className="mt-1 text-xs text-destructive underline-offset-4 hover:underline"
              onClick={onRetryScope}
            >
              Project scope unavailable · Retry
            </button>
          )}
        </div>
      )}
      <div className="relative min-w-56 flex-1 sm:min-w-64">
        <form className="flex gap-2" onSubmit={onSubmitSearch}>
          <label className="sr-only" htmlFor="knowledge-search">
            Search knowledge
          </label>
          <Input
            id="knowledge-search"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Search knowledge"
            disabled={searchPending}
          />
          <Button
            type="submit"
            variant="outline"
            size="icon"
            className="size-12 shrink-0"
            aria-label="Search knowledge"
            disabled={!online || searchPending || !search.trim()}
          >
            <Search className="size-4" />
          </Button>
        </form>
        {(searchResults.length > 0 || searchMessage) && (
          <div className="absolute top-full right-0 left-0 z-10 mt-2 max-h-80 overflow-y-auto rounded-lg border border-border bg-popover shadow-lg">
            {searchMessage && (
              <p role="alert" className="px-3 py-3 text-sm text-muted-foreground">
                {searchMessage}
              </p>
            )}
            {searchResults.map((result) => {
              const document = documents.get(result.documentId);
              if (!document) return null;
              const findings = claimsForDocument(claims, result.documentId);
              const visibleFindings = findings.slice(0, MAX_SEARCH_FINDINGS);
              const extraCount = findings.length - visibleFindings.length;
              return (
                <div
                  key={result.documentId}
                  className="border-b border-border/70 last:border-b-0"
                >
                  <button
                    type="button"
                    className="min-h-11 w-full px-3 py-2 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                    onClick={() =>
                      onSelectSearchResult({
                        kind: "document",
                        id: result.documentId,
                      })
                    }
                  >
                    <span className="block text-sm font-medium">
                      {documentTitle(document)}
                    </span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">
                      {findingsCountLabel(findings.length)}
                    </span>
                  </button>
                  {visibleFindings.length > 0 && (
                    <ul>
                      {visibleFindings.map((claim) => (
                        <li key={claim.id}>
                          <button
                            type="button"
                            className="min-h-11 w-full px-3 py-2 pl-6 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:bg-muted focus-visible:text-foreground focus-visible:outline-none"
                            onClick={() =>
                              onSelectSearchResult({ kind: "claim", id: claim.id })
                            }
                          >
                            {claimLabel(claim)}
                          </button>
                        </li>
                      ))}
                      {extraCount > 0 && (
                        <li className="px-3 pb-2 pl-6 text-xs text-muted-foreground">
                          {extraCount === 1
                            ? "1 more finding in the document"
                            : `${extraCount} more findings in the document`}
                        </li>
                      )}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="size-12"
        aria-label="Import knowledge"
        onClick={onImport}
        disabled={!online || importPending}
        title={!online ? "Reconnect before importing knowledge" : "Import knowledge"}
      >
        <FileUp className="size-4" />
      </Button>
    </div>
  );
}

function KnowledgeDetailsSheet({
  selection,
  document,
  claim,
  documents,
  findings,
  online,
  pending,
  onOpenChange,
  onSelectFinding,
  onDelete,
}: {
  selection?: KnowledgeSelection;
  document?: KnowledgeDocumentWithProvenance;
  claim?: EvidenceClaim;
  documents: ReadonlyMap<string, KnowledgeDocumentWithProvenance>;
  findings: EvidenceClaim[];
  online: boolean;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onSelectFinding: (id: string) => void;
  onDelete: (selection: KnowledgeSelection) => void;
}) {
  const title = document ? documentTitle(document) : "Claim";
  const description = document
    ? documentName(document)
    : claim?.summary || claim?.assertion || "Knowledge claim";
  return (
    <Sheet open={Boolean(selection)} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 p-0 sm:max-w-xl">
        <SheetHeader className="border-b border-border px-6 py-6 pr-16 sm:px-8 sm:pr-20">
          <SheetTitle className="font-display text-2xl font-medium">{title}</SheetTitle>
          <SheetDescription className="break-words">{description}</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-2 sm:px-8">
          {document && (
            <DocumentDetails
              document={document}
              findings={findings}
              onSelectFinding={onSelectFinding}
            />
          )}
          {claim && <ClaimDetails claim={claim} documents={documents} />}
        </div>
        <SheetFooter>
          <Button
            type="button"
            variant="destructive"
            className="min-h-11 w-full sm:w-auto sm:self-end"
            disabled={!online || pending}
            onClick={() => {
              if (selection && online) onDelete(selection);
            }}
            title={!online ? "Reconnect before deleting knowledge" : "Delete knowledge"}
          >
            <Trash2 className="size-4" />
            {pending ? "Deleting…" : "Delete"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function DocumentDetails({
  document,
  findings,
  onSelectFinding,
}: {
  document: KnowledgeDocumentWithProvenance;
  findings: EvidenceClaim[];
  onSelectFinding: (id: string) => void;
}) {
  return (
    <>
      <dl className="divide-y divide-border/70">
        <DetailRow
          label="Type"
          value={isAnalysisSummary(document) ? "Analysis" : "Document"}
        />
        <DetailRow label="Name" value={documentName(document)} />
        {document.mimeType && <DetailRow label="Format" value={document.mimeType} />}
        {documentState(document) && (
          <DetailRow
            label="State"
            value={knowledgeState(documentState(document)) || "—"}
          />
        )}
        {document.capturedAt && (
          <DetailRow label="Captured" value={formatDateTime(document.capturedAt)} />
        )}
        {document.knownAt && (
          <DetailRow label="Known at" value={formatDateTime(document.knownAt)} />
        )}
        {document.createdAt && (
          <DetailRow label="Added" value={formatDateTime(document.createdAt)} />
        )}
        {document.sourceRevision && (
          <DetailRow label="Revision" value={document.sourceRevision} breakAll />
        )}
        {document.error && <DetailRow label="Error" value={document.error} />}
      </dl>
      <section className="border-t border-border/70 py-5">
        <h3 className="text-sm font-medium">Extracted findings</h3>
        {findings.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            No findings have been extracted from this document.
          </p>
        ) : (
          <ul className="mt-2 divide-y divide-border/70">
            {findings.map((finding) => (
              <li key={finding.id}>
                <button
                  type="button"
                  className="min-h-11 w-full py-3 text-left text-sm hover:underline"
                  onClick={() => onSelectFinding(finding.id)}
                >
                  <span className="block">{claimLabel(finding)}</span>
                  {finding.status && (
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {knowledgeState(finding.status)}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function ClaimDetails({
  claim,
  documents,
}: {
  claim: EvidenceClaim;
  documents: ReadonlyMap<string, KnowledgeDocumentWithProvenance>;
}) {
  const quote =
    claim.quote || (typeof claim.source === "object" ? claim.source.quote : undefined);
  return (
    <div>
      <div className="space-y-5 py-5">
        {(claim.summary || claim.assertion) && (
          <p className="text-lg leading-7">{claim.summary || claim.assertion}</p>
        )}
        {quote && (
          <blockquote className="border-l-2 border-primary/40 pl-4 text-sm leading-6 text-muted-foreground">
            {quote}
          </blockquote>
        )}
      </div>
      <dl className="divide-y divide-border/70 border-t border-border/70">
        {claim.reportedState || claim.state ? (
          <DetailRow label="State" value={claim.reportedState || claim.state || "—"} />
        ) : null}
        {claim.value !== undefined && (
          <DetailRow
            label="Value"
            value={`${String(claim.value)}${claim.unit ? ` ${claim.unit}` : ""}`}
          />
        )}
        <DetailRow label="Source" value={claimSourceLabel(claim, documents)} />
        {claim.page !== undefined && (
          <DetailRow label="Page" value={String(claim.page)} />
        )}
        {claim.sourceRevision && (
          <DetailRow label="Revision" value={claim.sourceRevision} breakAll />
        )}
        {claimDocumentId(claim) && (
          <DetailRow label="Document id" value={claimDocumentId(claim) || "—"} breakAll />
        )}
        {claim.status && (
          <DetailRow label="State" value={knowledgeState(claim.status) || "—"} />
        )}
        <DetailRow label="Claim id" value={claim.id} breakAll />
      </dl>
    </div>
  );
}

function DetailRow({
  label,
  value,
  breakAll = false,
}: {
  label: string;
  value: ReactNode;
  breakAll?: boolean;
}) {
  return (
    <div className="grid gap-1 py-4 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className={cn("text-sm", breakAll && "break-all")}>{value}</dd>
    </div>
  );
}

function DeleteKnowledgeDialog({
  target,
  label,
  online,
  pending,
  error,
  onOpenChange,
  onConfirm,
}: {
  target?: KnowledgeSelection;
  label: string;
  online: boolean;
  pending: boolean;
  error: Error | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={Boolean(target)} onOpenChange={onOpenChange}>
      <DialogContent className="w-[calc(100%-2rem)] max-w-md">
        <DialogHeader>
          <DialogTitle>Delete knowledge?</DialogTitle>
          <DialogDescription className="break-words">
            Delete “{label}”? This cannot be undone.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {getErrorMessage(error, "The knowledge item could not be deleted.")}
          </p>
        )}
        {!online && <p className="text-sm text-muted-foreground">Reconnect to delete.</p>}
        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            className="min-h-11"
            onClick={() => onOpenChange(false)}
            disabled={pending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            className="min-h-11"
            onClick={onConfirm}
            disabled={!online || pending}
          >
            <Trash2 className="size-4" />
            {pending ? "Deleting…" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ImportProgressCard({
  job,
  status,
  statusError,
  collapsed,
  online,
  retryPending,
  retryError,
  onToggle,
  onRetry,
  onDismiss,
}: {
  job: ImportJobState;
  status?: KnowledgeImportJob;
  statusError: Error | null;
  collapsed: boolean;
  online: boolean;
  retryPending: boolean;
  retryError: Error | null;
  onToggle: () => void;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const progress = Math.min(100, Math.max(0, status?.progress ?? 0));
  const failed = isFailedImportStatus(status?.status) || Boolean(statusError);
  const complete = status?.status === "completed";
  const stage = complete
    ? "Imported"
    : failed
      ? "Import failed"
      : humanize(status?.stage || status?.status || "queued");
  const error = statusError
    ? getErrorMessage(statusError, "Import status could not be loaded.")
    : jobErrorMessage(status) || (retryError ? getErrorMessage(retryError) : undefined);
  return (
    <div className="fixed right-4 bottom-4 z-40 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-border bg-card p-3 shadow-xl">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="min-w-0 flex-1 rounded-lg px-1 py-1 text-left focus-visible:outline-2 focus-visible:outline-ring"
          aria-expanded={!collapsed}
          onClick={onToggle}
        >
          <span className="block truncate text-sm font-medium">{job.fileName}</span>
          <span className="mt-0.5 block text-xs text-muted-foreground">{stage}</span>
        </button>
        {isTerminalImportStatus(status?.status) && (
          <button
            type="button"
            className="flex size-10 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Dismiss import status"
            onClick={onDismiss}
          >
            <X className="size-4" />
          </button>
        )}
      </div>
      {!collapsed && (
        <div className="space-y-3 pt-3">
          <div
            className="h-1.5 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-label="Import progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <div
              className={cn(
                "h-full rounded-full bg-primary transition-[width]",
                failed && "bg-destructive",
              )}
              style={{ width: `${progress}%` }}
            />
          </div>
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          )}
          {failed && (
            <Button
              type="button"
              variant="outline"
              className="min-h-10 w-full"
              onClick={onRetry}
              disabled={!online || retryPending || !job.file}
              title={!job.file ? "Re-select the file to retry this import" : undefined}
            >
              {retryPending ? "Starting…" : "Retry upload"}
            </Button>
          )}
        </div>
      )}
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
