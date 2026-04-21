import pandas as pd

from app.engine.merge_engine import merge_batches


def test_merge_batches_replaces_rows_with_same_merge_key() -> None:
    batch_a = pd.DataFrame([
        {"identity_key": "A", "value": 1},
        {"identity_key": "B", "value": 2},
    ])
    batch_b = pd.DataFrame([
        {"identity_key": "B", "value": 99},
        {"identity_key": "C", "value": 3},
    ])

    merged = merge_batches([batch_a, batch_b], merge_keys=["identity_key"])

    records = merged.sort_values("identity_key").to_dict(orient="records")
    assert records == [
        {"identity_key": "A", "value": 1},
        {"identity_key": "B", "value": 99},
        {"identity_key": "C", "value": 3},
    ]


def test_merge_batches_requires_all_merge_keys() -> None:
    batch = pd.DataFrame([{"value": 1}])

    try:
        merge_batches([batch], merge_keys=["identity_key"])
    except ValueError as exc:
        assert "identity_key" in str(exc)
    else:
        raise AssertionError("Expected merge_batches to reject missing merge keys")
