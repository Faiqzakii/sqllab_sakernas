from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd


def _validate_identity_payload(payload: dict[str, Any], required_identity_columns: list[str]) -> None:
    missing = [column for column in required_identity_columns if column not in payload]
    if missing:
        raise ValueError(f"Missing required identity columns: {', '.join(missing)}")


def run_python_rule(
    snapshot: pd.DataFrame,
    rule_fn: Callable[[pd.DataFrame], list[dict[str, Any]]],
    required_identity_columns: list[str],
) -> list[dict[str, Any]]:
    normalized_snapshot = snapshot.astype(object).where(pd.notna(snapshot), None)
    findings = rule_fn(normalized_snapshot)
    for finding in findings:
        _validate_identity_payload(finding["identity_payload"], required_identity_columns)
        if "identity_key" not in finding:
            raise ValueError("Missing required field: identity_key")
    return findings


def run_sql_like_rule(
    findings: list[dict[str, Any]],
    required_identity_columns: list[str],
) -> list[dict[str, Any]]:
    for finding in findings:
        _validate_identity_payload(finding["identity_payload"], required_identity_columns)
        identity_key = finding["identity_payload"].get("identity_key")
        if identity_key is None:
            raise ValueError("Missing required identity columns: identity_key")
        finding.setdefault("identity_key", identity_key)
    return findings
