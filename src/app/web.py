from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.findings import _sample_hits
from app.services.findings import aggregate_identity_findings
from app.services.runs import create_run_with_steps


router = APIRouter()


@router.get("/ui/runs", response_class=HTMLResponse)
def runs_page() -> str:
    run = create_run_with_steps(1, ["superset_sql", "local_python"])
    items = "".join(f"<li>{step.step_type} - {step.status}</li>" for step in run.steps)
    return (
        "<html><body>"
        "<h1>Runs</h1>"
        f"<p>Status: {run.status}</p>"
        f"<ul>{items}</ul>"
        "</body></html>"
    )


@router.get("/ui/identity-findings", response_class=HTMLResponse)
def identity_findings_page() -> str:
    aggregates = aggregate_identity_findings(_sample_hits())
    rows = "".join(
        f"<tr><td>{item.identity_key}</td><td>{item.highest_severity}</td><td>{', '.join(str(rule_id) for rule_id in item.rule_ids)}</td></tr>"
        for item in aggregates
    )
    return (
        "<html><body>"
        "<h1>Identity Findings</h1>"
        "<table><thead><tr><th>Identity</th><th>Highest Severity</th><th>Rules</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "</body></html>"
    )
