"""Publish derived analysis context without promoting model output to evidence."""

from typing import Any


def publish_analysis_context(store: Any, organization_id: str, analysis: dict[str, Any]) -> None:
    analysis_id = str(analysis["id"])
    document_id = f"analysis-context:{analysis_id}"
    if store.get_record(organization_id, "knowledge_documents", document_id) is not None:
        return  # Never resurrect deleted context on a retry.
    explanation = analysis.get("explanation", {})
    summary = explanation.get("shortSummary") or explanation.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return
    completed = analysis.get("completedAt") or analysis.get("asOf")
    store.save_record(
        organization_id,
        "knowledge_documents",
        document_id,
        {
            "id": document_id,
            "organizationId": organization_id,
            "projectIds": [str(p["id"]) for p in analysis.get("projects", [])],
            "fileName": f"Analysis · {str(completed)[:10]}",
            "summary": summary,
            "mediaType": "text/plain",
            "status": "extracted",
            "active": True,
            "sourceMode": analysis.get("sourceMode"),
            "sourceRevision": analysis_id,
            "knownAt": completed,
            "capturedAt": completed,
            "provenance": {
                "sourceType": "analysis_summary",
                "analysisId": analysis_id,
                "derived": True,
                "notMeasuredEvidence": True,
            },
        },
    )
