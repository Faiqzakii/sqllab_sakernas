from fastapi.testclient import TestClient

from app.main import create_app


def test_request_defaults_to_viewer_role() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/auth/session")

    assert response.status_code == 200
    assert response.json()["data"] == {"role": "viewer", "authenticated": False}


def test_successful_login_elevates_session_to_admin() -> None:
    client = TestClient(create_app())

    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    session = client.get("/api/v1/auth/session")

    assert login.status_code == 200
    assert session.status_code == 200
    assert session.json()["data"] == {"role": "admin", "authenticated": True}


def test_logout_restores_viewer_session() -> None:
    client = TestClient(create_app())

    client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    logout = client.post("/api/v1/auth/logout")
    session = client.get("/api/v1/auth/session")

    assert logout.status_code == 200
    assert logout.json()["data"] == {"role": "viewer", "authenticated": False}
    assert session.json()["data"] == {"role": "viewer", "authenticated": False}
