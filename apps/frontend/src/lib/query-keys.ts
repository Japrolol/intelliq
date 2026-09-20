export const queryKeys = {
  overview: (organizationId: string) => ["overview", organizationId] as const,
  analysis: (organizationId: string, analysisId: string) =>
    ["analysis", organizationId, analysisId] as const,
  graph: (organizationId: string, analysisId: string) =>
    ["analysis-graph", organizationId, analysisId] as const,
  analyses: (organizationId: string) => ["analyses", organizationId] as const,
  knowledge: (organizationId: string) => ["knowledge", organizationId] as const,
  imports: (organizationId: string, importId?: string) =>
    ["imports", organizationId, importId || "new"] as const,
  settings: (organizationId: string) => ["settings", organizationId] as const,
  job: (organizationId: string, jobId: string) => ["job", organizationId, jobId] as const,
};
