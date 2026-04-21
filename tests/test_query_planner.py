from app.engine.query_planner import build_subquery_specs
from app.sample_queries import SIMULATED_COMPLETE_DATA_BATCHING, SIMULATED_COMPLETE_DATA_SQL_TEMPLATE


def test_build_subquery_specs_for_explicit_list_batches() -> None:
    specs = build_subquery_specs(
        sql_template="select * from households where region = '{{ region }}'",
        params={"year": 2025},
        batching_strategy={
            "type": "explicit_list",
            "param": "region",
            "values": ["6501", "6503"],
        },
    )

    assert [spec.batch_order for spec in specs] == [0, 1]
    assert "6501" in specs[0].rendered_sql
    assert "6503" in specs[1].rendered_sql


def test_simulated_complete_data_query_fans_out_into_five_queries() -> None:
    specs = build_subquery_specs(
        sql_template=SIMULATED_COMPLETE_DATA_SQL_TEMPLATE,
        params={},
        batching_strategy=SIMULATED_COMPLETE_DATA_BATCHING,
    )

    assert len(specs) == 5
    assert "art.level_2_code='01'" in specs[0].rendered_sql
    assert "art.level_2_code='02'" in specs[1].rendered_sql
    assert "art.level_2_code='03'" in specs[2].rendered_sql
    assert "art.level_2_code='04'" in specs[3].rendered_sql
    assert "art.level_2_code='71'" in specs[4].rendered_sql


def test_build_subquery_specs_for_range_split() -> None:
    specs = build_subquery_specs(
        sql_template="select * from households where seq >= {{ start }} and seq < {{ end }}",
        params={},
        batching_strategy={
            "type": "range_split",
            "start": 1,
            "stop": 7,
            "step": 3,
            "start_param": "start",
            "end_param": "end",
        },
    )

    assert len(specs) == 2
    assert "seq >= 1" in specs[0].rendered_sql
    assert "seq < 4" in specs[0].rendered_sql
    assert "seq >= 4" in specs[1].rendered_sql
    assert "seq < 7" in specs[1].rendered_sql
