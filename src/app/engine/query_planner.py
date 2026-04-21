from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SubquerySpec:
    batch_order: int
    batch_params: dict[str, Any]
    rendered_sql: str


def _render_sql(template: str, params: dict[str, Any]) -> str:
    rendered = template
    for key, value in params.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", str(value))
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered


def build_subquery_specs(
    sql_template: str,
    params: dict[str, Any],
    batching_strategy: dict[str, Any],
) -> list[SubquerySpec]:
    strategy_type = batching_strategy["type"]

    if strategy_type == "explicit_list":
        param_name = batching_strategy["param"]
        values = list(batching_strategy["values"])
        specs: list[SubquerySpec] = []
        for index, value in enumerate(values):
            batch_params = {**params, param_name: value}
            specs.append(
                SubquerySpec(
                    batch_order=index,
                    batch_params=batch_params,
                    rendered_sql=_render_sql(sql_template, batch_params),
                )
            )
        return specs

    if strategy_type == "range_split":
        start = int(batching_strategy["start"])
        stop = int(batching_strategy["stop"])
        step = int(batching_strategy["step"])
        start_param = batching_strategy["start_param"]
        end_param = batching_strategy["end_param"]
        specs: list[SubquerySpec] = []
        batch_order = 0
        current = start
        while current < stop:
            end_value = min(current + step, stop)
            batch_params = {**params, start_param: current, end_param: end_value}
            specs.append(
                SubquerySpec(
                    batch_order=batch_order,
                    batch_params=batch_params,
                    rendered_sql=_render_sql(sql_template, batch_params),
                )
            )
            batch_order += 1
            current = end_value
        return specs

    raise ValueError(f"Unsupported batching strategy: {strategy_type}")
