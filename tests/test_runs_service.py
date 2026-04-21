from app.services.runs import create_run_with_steps


def test_create_run_with_steps_builds_pending_run_plan() -> None:
    run = create_run_with_steps(
        job_definition_id=1,
        step_types=["superset_sql", "local_python"],
    )

    assert run.status == "pending"
    assert [step.step_type for step in run.steps] == ["superset_sql", "local_python"]
    assert all(step.status == "pending" for step in run.steps)
