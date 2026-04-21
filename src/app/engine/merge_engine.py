from __future__ import annotations

import pandas as pd


def merge_batches(batches: list[pd.DataFrame], merge_keys: list[str]) -> pd.DataFrame:
    if not merge_keys:
        raise ValueError("merge_keys must not be empty")

    merged_by_key: dict[tuple[object, ...], dict[str, object]] = {}
    ordered_columns: list[str] = []

    for batch in batches:
        missing_keys = [key for key in merge_keys if key not in batch.columns]
        if missing_keys:
            raise ValueError(f"Missing merge keys in batch: {', '.join(missing_keys)}")

        for column in batch.columns:
            if column not in ordered_columns:
                ordered_columns.append(column)

        for row in batch.to_dict(orient="records"):
            key = tuple(row[key_name] for key_name in merge_keys)
            merged_by_key[key] = row

    if not merged_by_key:
        return pd.DataFrame(columns=ordered_columns)

    merged_rows = list(merged_by_key.values())
    return pd.DataFrame(merged_rows, columns=ordered_columns)
