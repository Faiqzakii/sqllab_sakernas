import pandas as pd

from app.engine.rules_engine import run_python_rule, run_sql_like_rule


def test_python_rule_execution_returns_normalized_findings() -> None:
    snapshot = pd.DataFrame(
        [
            {"identity_key": "A", "household_number": None},
            {"identity_key": "B", "household_number": "01"},
        ]
    )

    def rule_fn(df: pd.DataFrame):
        findings = []
        for row in df.to_dict(orient="records"):
            if row["household_number"] is None:
                findings.append(
                    {
                        "identity_key": row["identity_key"],
                        "severity": "warn",
                        "message": "missing household number",
                        "identity_payload": row,
                    }
                )
        return findings

    findings = run_python_rule(snapshot, rule_fn, required_identity_columns=["identity_key", "household_number"])

    assert findings == [
        {
            "identity_key": "A",
            "severity": "warn",
            "message": "missing household number",
            "identity_payload": {"identity_key": "A", "household_number": None},
        }
    ]


def test_sql_like_rule_execution_requires_identity_columns() -> None:
    findings = [{"severity": "warn", "message": "bad row", "identity_payload": {"household_number": "01"}}]

    try:
        run_sql_like_rule(findings, required_identity_columns=["identity_key", "household_number"])
    except ValueError as exc:
        assert "identity_key" in str(exc)
    else:
        raise AssertionError("Expected missing identity column validation to fail")
