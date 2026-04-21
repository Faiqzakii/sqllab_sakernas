from app.main import create_app


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app()

    assert app.title == "Superset SQL Lab Platform"
