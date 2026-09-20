import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowUpRight } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Card, ErrorPanel, Skeleton } from "../components/ui";
import { Button } from "../components/ui/button";
import { getErrorMessage, request, scopedPath } from "../lib/api";
import { formatDateTime, formatPercent } from "../lib/format";
import type { Organization, Outcome } from "../lib/types";
import { cn } from "../lib/utils";
import { KnowledgePage } from "./KnowledgePage";

const PROJECT_TABS = [
  { id: "analysis", label: "Analysis history" },
  { id: "knowledge", label: "Knowledge base" },
] as const;
type ProjectTab = (typeof PROJECT_TABS)[number]["id"];

interface ProjectDetail {
  project: { id: string; name: string };
  analyses: Array<{ id: string; completedAt?: string; outcome?: Outcome }>;
  asOf: string;
  sourceMode: "live" | "fixture";
}

export function ProjectPage({ organization }: { organization: Organization }) {
  const { projectId = "" } = useParams();
  return <ProjectDetailView key={organization.id + ":" + projectId} organization={organization} projectId={projectId} />;
}

function ProjectDetailView({ organization, projectId }: { organization: Organization; projectId: string }) {
  const [activeTab, setActiveTab] = useState<ProjectTab>(PROJECT_TABS[0].id);
  const { data, isLoading, error, refetch } = useQuery<ProjectDetail>({
    queryKey: ["project", organization.id, projectId],
    queryFn: () => request(scopedPath(organization.id, "/projects/" + encodeURIComponent(projectId))),
    staleTime: 30_000,
  });

  return (
    <div className="space-y-6">
      <Button asChild variant="ghost" className="min-h-11 px-0">
        <Link to="/projects"><ArrowLeft /> All projects</Link>
      </Button>
      {isLoading && <Skeleton className="min-h-72" />}
      {error && <ErrorPanel title="Project could not be loaded" message={getErrorMessage(error, "Could not read the project from Timecue.")} onRetry={() => void refetch()} />}
      {data && !error && <>
        <header className="space-y-2">
          <h1 className="break-words text-3xl font-semibold tracking-tight">{data.project.name}</h1>
          <p className="text-xs text-muted-foreground">
            {data.sourceMode === "fixture" ? "Synthetic demo" : "Timecue data"} · Updated {formatDateTime(data.asOf)}
          </p>
        </header>
        <div role="tablist" aria-label="Project sections" className="flex gap-2 border-b border-border">
          {PROJECT_TABS.map((tab, index) => (
            <Button key={tab.id} id={"project-tab-" + tab.id} role="tab" type="button" variant="ghost"
              aria-selected={activeTab === tab.id} aria-controls={"project-panel-" + tab.id} tabIndex={activeTab === tab.id ? 0 : -1}
              className={cn("min-h-12 rounded-none border-b-2 border-transparent px-3 text-muted-foreground", activeTab === tab.id && "border-primary text-foreground")}
              onClick={() => setActiveTab(tab.id)}
              onKeyDown={(event) => {
                let next = index;
                if (event.key === "ArrowRight") next = (index + 1) % PROJECT_TABS.length;
                else if (event.key === "ArrowLeft") next = (index + PROJECT_TABS.length - 1) % PROJECT_TABS.length;
                else if (event.key === "Home") next = 0;
                else if (event.key === "End") next = PROJECT_TABS.length - 1;
                else return;
                event.preventDefault();
                setActiveTab(PROJECT_TABS[next].id);
                document.getElementById("project-tab-" + PROJECT_TABS[next].id)?.focus();
              }}>
              {tab.label}
            </Button>
          ))}
        </div>
        <section id="project-panel-analysis" role="tabpanel" aria-labelledby="project-tab-analysis" hidden={activeTab !== PROJECT_TABS[0].id} tabIndex={0}>
          <Card className="gap-0 p-0">
            <ul className="divide-y divide-border">
              {data.analyses.map((analysis) => (
                <li key={analysis.id}>
                  <Link to={"/analyses/" + encodeURIComponent(analysis.id)} className="flex min-h-16 items-center justify-between gap-3 p-4 hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring">
                    <div className="min-w-0">
                      <p className="font-medium">{analysis.completedAt ? formatDateTime(analysis.completedAt) : "Completion date not returned"}</p>
                      <p className="mt-1 break-all text-xs text-muted-foreground">Run {analysis.id}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-3">
                      <span className="text-right text-xs text-muted-foreground">Baseline delay risk<br /><span className="text-sm text-foreground">{formatPercent(analysis.outcome?.delayProbability)}</span></span>
                      <ArrowUpRight className="size-4" aria-hidden="true" />
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
            {!data.analyses.length && <p className="p-6 text-sm text-muted-foreground">No analysis history for this project yet.</p>}
          </Card>
        </section>
        <section id="project-panel-knowledge" role="tabpanel" aria-labelledby="project-tab-knowledge" hidden={activeTab !== PROJECT_TABS[1].id} tabIndex={0}>
          {activeTab === PROJECT_TABS[1].id && <KnowledgePage organization={organization} projectId={projectId} embedded />}
        </section>
      </>}
    </div>
  );
}
