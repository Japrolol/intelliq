export type DataMode = "live" | "fixture";
export type ReadinessState = "ready" | "needs_inputs" | "blocked";
export type AnalysisMode = "baseline" | "recovery";
export type AnalysisStatus =
  "queued" | "running" | "complete" | "completed" | "failed" | "cancelled";
export type SignalReviewStatus = "pending" | "proposed" | "confirmed" | "rejected";
export type ImplementationState =
  "reported" | "partially_implemented" | "implemented" | "not_implemented";
export type CheckpointState = "not_due" | "needs_data" | "met" | "breached";
export type CheckpointComparator = "at_most" | "at_least";

export interface RuntimeConfig {
  sourceMode: DataMode;
  synthetic: boolean;
}

export interface Organization {
  id: string;
  name: string;
}

export interface SessionUser {
  id: string;
  name?: string;
  email?: string;
  role?: string;
}

export interface Session {
  user: SessionUser;
  organizations?: Organization[];
  sourceMode?: DataMode;
  synthetic?: boolean;
}

export interface ReadinessIssue {
  id?: string;
  code?: string;
  title?: string;
  detail?: string;
  severity?: "info" | "warning" | "blocking";
  projectId?: string;
}

export interface PortfolioProject {
  id: string;
  name: string;
  targetFinishAt?: string;
  priority?: number;
  readiness?: ReadinessState;
  readinessIssues?: ReadinessIssue[];
  delayProbability?: number;
  expectedPositiveDelayDays?: number;
  finishP50?: string;
  status?: string;
}

export interface PortfolioResponse {
  sourceMode: DataMode;
  asOf?: string;
  projects: PortfolioProject[];
  readinessIssues: ReadinessIssue[];
  readinessStatus?: ReadinessState;
}

export interface PlanningProject {
  id: string;
  name?: string;
  targetFinishAt?: string;
  priority?: number;
}

export interface PlanningTask {
  id: string;
  projectId: string;
  title?: string;
  status?: string;
  predecessorIds?: string[];
  requiredSpecialtyId?: string;
  remainingPersonHours?: {
    optimistic?: number;
    mostLikely?: number;
    pessimistic?: number;
  };
  minCrew?: number;
  maxCrew?: number;
  earliestStartAt?: string;
}

export interface PlanningWorker {
  id: string;
  name?: string;
  specialtyIds?: string[];
  homeProjectId?: string;
  canTransfer?: boolean;
}

export interface TransferAssumption {
  fromProjectId?: string;
  toProjectId?: string;
  outboundTravelHours?: number;
  returnTravelHours?: number;
  setupHours?: number;
}

export interface PlanningInputsResponse {
  version: string | number;
  projects: PlanningProject[];
  tasks: PlanningTask[];
  workers: PlanningWorker[];
  transfers?: TransferAssumption[];
  transferAssumptions?: TransferAssumption[];
}

export interface Outcome {
  projectId?: string;
  projectName?: string;
  targetFinishAt?: string;
  deadline?: string;
  p10?: string;
  p50?: string;
  p90?: string;
  finishP10?: string;
  delayProbability?: number;
  delayRisk?: number;
  expectedPositiveDelayDays?: number;
  finishP50?: string;
  finishP90?: string;
  unfinishedCount?: number;
  completionCount?: number;
  completedSampleCount?: number;
  censoringCount?: number;
  sampleCount?: number;
  targetBufferDays?: number;
  bufferDeltaDays?: number;
  missingReason?: string;
  censoringReason?: string;
  coverageStart?: string;
  coverageEnd?: string;
  censored?: boolean;
  knownLateCount?: number;
  unknownCensoredCount?: number;
  censoring?: {
    censored?: boolean;
    unfinishedCount?: number;
    unknownBeforeTargetCount?: number;
    knownLateUnfinishedCount?: number;
    horizonEnd?: string;
    reason?: string;
  };
  histogramCoverage?: {
    from?: string;
    to?: string;
    unfinishedBucket?: boolean;
  };
  capacityBasis?: string;
  capacityByDay?: CapacityPoint[];
  finishHistogram?: HistogramBin[];
}

export interface CapacityPoint {
  date?: string;
  day?: string;
  availableHours?: number;
  usedHours?: number;
  availablePersonHours?: number;
  regularAvailablePersonHours?: number;
  overtimeAvailablePersonHours?: number;
  transferAvailablePersonHours?: number;
  usedPersonHours?: number;
  effectiveWorkHours?: number;
  overtimeUsedHours?: number;
  transferProductiveHours?: number;
  transferTravelHours?: number;
  transferSetupHours?: number;
  transferNonProductiveHours?: number;
  transferHours?: number;
  setupHours?: number;
  overtimeHours?: number;
}

export interface HistogramBin {
  bucket?: string;
  label?: string;
  date?: string;
  count?: number;
  probability?: number;
  unfinished?: boolean;
  censored?: boolean;
  missingReason?: string;
}

export interface ProjectOutcome extends Outcome {
  projectId?: string;
  projectName?: string;
}

export interface StrategyTransfer {
  workerId?: string;
  workerName?: string;
  fromProjectId?: string;
  fromProjectName?: string;
  toProjectId?: string;
  toProjectName?: string;
  windowStartAt?: string;
  windowEndAt?: string;
  setupHours?: number;
  travelHours?: number;
  donorBufferDays?: number;
}

export interface Strategy {
  explanation?: OptionExplanation;
  id: string;
  name?: string;
  label?: string;
  strategy?: string;
  strategyName?: string;
  strategyRoles?: string[];
  summary?: string;
  rationale?: string;
  target?: Outcome;
  donor?: Outcome;
  outcomesByProject?: Record<string, Outcome> | Array<Outcome & { projectId?: string }>;
  transfer?: StrategyTransfer;
  exactChanges?: string[];
  actions?: Array<Record<string, unknown>>;
  guardrailChecks?: Record<string, unknown>;
  affectedProjectIds?: string[];
  feasible?: boolean;
  permitsApproval?: boolean;
  approvalBlockers?: string[];
  warnings?: string[];
  labels?: string[];
  title?: string;
  rank?: number;
  status?: string;
  targetProjectId?: string;
  donorProjectId?: string;
  impactsByProject?: Record<string, ProjectOutcome>;
  manualStepCount?: number;
  apiStepCount?: number;
}

export interface Diagnostic {
  id?: string;
  code?: string;
  kind?: string;
  message?: string;
  label?: string;
  detail?: string;
  severity?: "info" | "warning" | "blocking";
  scenarioIds?: string[];
  projectId?: string;
  taskId?: string;
  workerId?: string;
  specialtyId?: string;
  observedInSamples?: number;
  sampleCount?: number;
  entityRefs?:
    | string[]
    | Array<{ type?: string; id?: string; label?: string }>
    | Record<string, string | { type?: string; id?: string; label?: string }>;
  affectedInterval?: { startsAt?: string; endsAt?: string };
  blockerRefs?: string[];
  sourceRefs?: string[];
}

export interface AnalysisResponse {
  createdAt?: string;
  completedAt?: string;
  id: string;
  projectId: string;
  targetProjectId?: string;
  mode: AnalysisMode;
  status: AnalysisStatus;
  targetProjectName?: string;
  baselineByProject?: Record<string, Outcome>;
  strategies?: Strategy[];
  diagnostics?: Diagnostic[];
  seed?: number;
  sampleCount?: number;
  snapshotRevision?: string;
  warnings?: string[];
  readinessIssues?: ReadinessIssue[];
  error?: string;
  sourceMode?: DataMode;
  generatedAt?: string;
  asOf?: string;
  providerStatus?: ProviderStatus | Record<string, unknown>;
  contextWarnings?: string[];
  evidenceStatus?: { status?: string; detail?: string } | string;
  projects?: PortfolioProject[];
  baseline?: ProjectOutcome | Record<string, ProjectOutcome>;
  projectOutcomes?: Record<string, ProjectOutcome> | ProjectOutcome[];
  scenarios?: Strategy[];
  selectedScenarioId?: string;
  recommendationId?: string;
  recommendation?: Recommendation;
  distributions?: DistributionSet;
  capacityByDay?: CapacityPoint[];
  assumptions?: Assumption[];
  sourceEvidence?: SourceEvidence[];
  history?: AnalysisHistoryPoint[];
  modelInfo?: { version?: string; seed?: number; sampleCount?: number };
  estimatedInputs?: EstimatedInput[];
  stale?: boolean;
  calibration?: CalibrationSnapshot;
  explanation?: AnalysisExplanation;
}

export interface AnalysisExplanation {
  summary?: string;
  summarySource?: "provider" | "deterministic" | string;
  statements?: string[];
  references?: string[];
  entityIds?: string[];
  limitations?: string[];
  providerStatus?: ProviderStatus | Record<string, unknown>;
}

export interface CalibrationSnapshot {
  version?: string | number;
  status?: string;
  meanHours?: number;
  updatedMeanHours?: number;
  priorMeanHours?: number;
  priorStrength?: number;
  totalSampleCount?: number;
  observationIds?: string[];
  updatedAt?: string;
}

export interface EstimatedInput {
  entityId: string;
  field: string;
  status: "estimated" | string;
  source?: string;
  detail?: string;
}

export interface Recommendation {
  id?: string;
  status?: string;
  strategyId?: string;
  title?: string;
  summary?: string;
  rationale?: string;
  affectedProjectIds?: string[];
}

export interface DistributionSet {
  finish?: HistogramBin[];
  completion?: HistogramBin[];
  byProject?: Record<string, HistogramBin[]>;
}

export interface Assumption {
  id?: string;
  label?: string;
  value?:
    | string
    | number
    | boolean
    | { optimistic?: number; mostLikely?: number; pessimistic?: number; unit?: string }
    | null;
  detail?: string;
  source?: string;
  confirmed?: boolean;
}

export interface SourceEvidence {
  id?: string;
  label?: string;
  detail?: string;
  sourceId?: string;
  sourceRevision?: string;
  confirmed?: boolean;
}

export interface AnalysisHistoryPoint {
  id?: string;
  createdAt?: string;
  selectedScenarioId?: string;
  label?: string;
  status?: string;
  projectOutcomes?: Record<string, ProjectOutcome>;
}

export interface ProviderStatus {
  status?: string;
  provider?: string;
  model?: string | null;
  configured?: boolean;
  enabled?: boolean;
  multimodal?: boolean;
  reason?: string | null;
  promptVersion?: string;
  schemaVersion?: string;
  message?: string;
  checkedAt?: string;
  coverageStart?: string;
  coverageEnd?: string;
  live?: boolean;
  state?: string;
  coverage?: Record<string, unknown> | string | null;
  coverageByProject?: Record<string, unknown> | null;
}

export interface PendingStep {
  id: string;
  title?: string;
  kind?: "api" | "manual" | "mixed" | string;
  status?: string;
  detail?: string;
  executionId?: string;
  projectId?: string;
  workerId?: string;
  transferConfirmed?: boolean;
  action?: Record<string, unknown>;
}

export interface OverviewResponse {
  analysis: AnalysisResponse | null;
  projects: PortfolioProject[];
  readinessIssues: ReadinessIssue[];
  pendingSteps: PendingStep[];
  pendingConfirmations?: EvidenceClaim[];
  sourceMode?: DataMode;
  generatedAt?: string;
  asOf?: string;
  providerStatus?: Record<string, ProviderStatus> | ProviderStatus;
  stale?: boolean;
  latestJob?: JobResponse | null;
}

export interface JobError {
  code?: string;
  message?: string;
  requiredAction?: string;
  retryable?: boolean;
}

export interface JobResponse {
  id: string;
  jobId?: string;
  status: AnalysisStatus | string;
  stage?: string;
  progress?: number;
  analysisId?: string;
  queuedAt?: string;
  startedAt?: string;
  completedAt?: string;
  updatedAt?: string;
  error?: string | JobError;
  actionableError?: string;
  result?: {
    analysisId?: string;
    status?: string;
    readinessIssues?: ReadinessIssue[];
    error?: string | JobError;
  };
}

export interface GraphNode {
  id: string;
  type: "project" | "task" | "worker" | "material" | string;
  label: string;
  projectId?: string;
  data?: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type:
    "prerequisite" | "assignment" | "shared_resource" | "material_requirement" | string;
  data?: Record<string, unknown>;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  projects: Array<{ id: string; name: string }>;
}

export interface ReviewStep {
  action?: Record<string, unknown>;
  id: string;
  title?: string;
  kind: "api" | "manual" | string;
  status?: string;
  detail?: string;
  projectId?: string;
  entityRefs?: string[];
  before?: Record<string, unknown>;
  after?: Record<string, unknown>;
}

export interface ReviewResponse {
  explanation?: OptionExplanation;
  analysisId: string;
  strategyId: string;
  reviewHash: string;
  steps: ReviewStep[];
  warnings: string[];
  rationale?: string;
}

export interface OptionExplanation extends AnalysisExplanation {
  contextNotes?: string[];
  comparisons?: Array<{ projectName: string; beforeRisk?: number; afterRisk?: number }>;
}

export interface ExecutionResponse {
  analysisId?: string;
  approvedAt?: string;
  id: string;
  status: string;
  steps: PendingStep[];
  message?: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface ExecutionOutcomeResponse {
  execution?: ExecutionResponse;
  calibration?: CalibrationSnapshot;
  calibrationChanged?: boolean;
  calibrationVersion?: string | number;
  laterForecastAnalysisId?: string;
  message?: string;
}

export interface KnowledgeDocument {
  id: string;
  name?: string;
  fileName?: string;
  projectId?: string;
  mimeType?: string;
  mediaType?: string;
  status?: string;
  extractionStatus?: string;
  capturedAt?: string;
  knownAt?: string;
  createdAt?: string;
  sourceRevision?: string;
  claimCount?: number;
  extraction?: {
    claimCount?: number;
    mode?: string;
    status?: string;
    limitations?: string[];
  };
  error?: string;
}

export interface EvidenceClaim {
  id: string;
  documentId?: string;
  sourceDocumentId?: string;
  projectId?: string;
  assertion?: string;
  summary?: string;
  reportedState?: string;
  state?: string;
  value?: string | number | boolean;
  unit?: string;
  quote?: string;
  page?: number;
  source?:
    | string
    | {
        documentId?: string;
        fileName?: string;
        mediaType?: string;
        quote?: string;
        page?: number;
      };
  sourceRevision?: string;
  status?: "proposed" | "confirmed" | "rejected" | "superseded" | string;
  planningImpact?: string;
  planningMutation?:
    | string
    | {
        status?: string;
        id?: string;
        planningVersion?: string | number;
        sourceRevision?: string;
        projectionKey?: string;
        taskId?: string;
        [key: string]: unknown;
      }
    | null;
}

export interface KnowledgeResponse {
  documents: KnowledgeDocument[];
  claims: EvidenceClaim[];
  providerStatus?: ProviderStatus;
}

export interface ImportCounts {
  projects?: number;
  tasks?: number;
  workers?: number;
  skills?: number;
  assignments?: number;
  planning?: number;
  created?: number;
  updated?: number;
  errors?: number;
  [key: string]: number | undefined;
}

export interface ImportRow {
  rowKey?: string;
  sourceRow?: number;
  sourceFile?: string;
  entity?: string;
  errors?: string[];
  id?: string;
  sheet?: string;
  rowNumber?: number;
  externalKey?: string;
  status?: string;
  message?: string | ImportIssue;
  entityType?: string;
  upstreamId?: string;
  values?: Record<string, unknown>;
}

export interface ImportIssue {
  code?: string;
  message?: string;
  rowKey?: string;
  entity?: string;
  sourceFile?: string;
  sourceRow?: number;
  detail?: string;
}

export type ImportIssueValue = string | ImportIssue;

export interface ImportPreviewResponse {
  id: string;
  status: string;
  rows: ImportRow[];
  errors: ImportIssueValue[];
  counts: ImportCounts;
  warnings?: ImportIssueValue[];
  assumptions?: Assumption[];
  createdAt?: string;
  updatedAt?: string;
  previewHash?: string;
  planningStatus?: string;
  message?: string;
}

export type ImportBatchResponse = ImportPreviewResponse & {
  receipts?: Array<{
    id?: string;
    rowId?: string;
    rowKey?: string;
    status?: string;
    detail?: string;
    message?: string;
    upstreamId?: string;
  }>;
  progress?: number;
};

export interface SettingsResponse {
  timezone?: string;
  readyBy?: string;
  analysisTime?: string;
  enabled?: boolean;
  nextRunAt?: string;
  priorityPolicy?: string;
  connectionStatus?: ProviderStatus;
  providers?: Record<string, boolean>;
  [key: string]: unknown;
}

export interface AnalysisReadinessResponse {
  status: "needs_inputs";
  readinessIssues: ReadinessIssue[];
  snapshotId?: string;
  sourceRevision?: string;
}

export interface Signal {
  id: string;
  projectId: string;
  taskId?: string;
  title?: string;
  quote?: string;
  evidenceQuotes?: Array<{ text: string; start?: number; end?: number }>;
  assertion?: string;
  state?: string;
  sourceName?: string;
  sourceRevision?: string;
  sourceDate?: string;
  status?: SignalReviewStatus;
  source?: {
    sourceId?: string;
    sourceRevision?: string;
    sourceKind?: string;
  };
  reviewStatus?: SignalReviewStatus;
  observation?: {
    kind?: string;
    assertion?: string;
    state?: string;
    evidenceQuotes?: Array<{ text: string; start?: number; end?: number }>;
    linkedTaskId?: string;
    reviewStatus?: SignalReviewStatus;
    planningChange?: string;
  };
  planningChange?: string;
}

export interface SignalsResponse {
  projectId?: string;
  reviewed?: Signal[];
  pending?: Signal[];
  signals: Signal[];
  sourceMode?: DataMode;
}

export interface DecisionContract {
  id: string;
  organizationId?: string;
  sourceMode?: DataMode;
  analysisId?: string;
  strategyId?: string;
  status?: string;
  createdAt?: string;
  updatedAt?: string;
  approvedAt?: string;
  snapshotId?: string;
  sourceRevision?: string;
  assumptionsVersion?: string | number;
  calibrationVersion?: string | number;
  modelVersion?: string;
  weatherRevision?: string;
  rationale?: string;
  managerRationale?: string;
  guardrails?: string[] | Record<string, unknown>;
  implementationStatus?: string;
  implementationState?: "not_applied" | string;
  appliedToTimecue?: boolean;
  message?: string;
  baselineOutcomes?: Record<string, Outcome> | Array<Outcome & { projectId?: string }>;
  selectedOutcomes?: Record<string, Outcome> | Array<Outcome & { projectId?: string }>;
  exactActions?: Array<Record<string, unknown>>;
  targetProjectName?: string;
  donorProjectName?: string;
  strategyLabel?: string;
  target?: Outcome;
  donor?: Outcome;
  checkpoints?: Array<{
    id?: string;
    label?: string;
    metric?: string;
    comparator?: CheckpointComparator;
    threshold?: number;
    evidenceRequirement?: string;
    status?: string;
    dueAt?: string;
  }>;
  observations?: DecisionObservation[];
  implementationEvents?: DecisionImplementationEvent[];
  checkpointEvaluations?: CheckpointEvaluation[];
}

export interface DecisionImplementationEvent {
  id: string;
  decisionId?: string;
  observationType?: string;
  type?: string;
  sourceMode?: DataMode;
  state?: ImplementationState | string;
  eventAt?: string;
  knownAt?: string;
  createdAt?: string;
  note?: string;
}

export interface DecisionObservation {
  id: string;
  decisionId?: string;
  observationType?: string;
  type?: string;
  sourceMode?: DataMode;
  eventAt?: string;
  knownAt?: string;
  createdAt?: string;
  workerId?: string;
  fromProjectId?: string;
  toProjectId?: string;
  setupHours?: number;
  acceptedForCalibration?: boolean;
  note?: string;
}

export interface CheckpointEvaluation {
  id: string;
  decisionId?: string;
  checkpointId?: string;
  state?: CheckpointState | string;
  evaluatedAt?: string;
  asOf?: string;
  observedValue?: number;
  evidence?: Record<string, unknown>;
  evidenceProvided?: boolean;
  sourceMode?: DataMode;
}

export interface CalibrationVersion {
  id?: string;
  version?: number;
  sourceMode?: DataMode;
  meanHours?: number;
  priorMeanHours?: number;
  priorStrength?: number;
  previousSampleCount?: number;
  acceptedObservationHours?: number[];
  updatedMeanHours?: number;
  totalSampleCount?: number;
  status?: string;
  assumption?: string;
  updatedAt?: string;
  createdAt?: string;
}

export interface CalibrationResponse extends CalibrationVersion {
  current?: CalibrationVersion;
  history?: CalibrationVersion[];
}

export interface ApiMessage {
  detail?:
    | string
    | {
        code?: string;
        message?: string;
      };
  message?: string;
}
