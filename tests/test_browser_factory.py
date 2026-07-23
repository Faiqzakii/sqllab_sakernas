from __future__ import annotations

from pathlib import Path

import app.engine.browser_factory as browser_factory


class FakeContext:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeCamoufoxManager:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.entered = False
        self.exited = False
        self.context = FakeContext()

    def __enter__(self) -> FakeContext:
        self.entered = True
        return self.context

    def __exit__(self, exc_type, exc, tb) -> None:
        self.exited = True


def test_resolve_profile_dir_defaults_to_repo_dot_camoufox(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(browser_factory.PROFILE_ENV_KEY, raising=False)
    monkeypatch.chdir(tmp_path)

    resolved = browser_factory.resolve_profile_dir()

    assert resolved == (tmp_path / ".camoufox-profile").resolve()


def test_resolve_profile_dir_honors_env(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "custom-profile"
    monkeypatch.setenv(browser_factory.PROFILE_ENV_KEY, str(custom))

    resolved = browser_factory.resolve_profile_dir()

    assert resolved == custom.resolve()


def test_launch_stealth_browser_passes_approved_defaults(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_camoufox(**kwargs: object) -> FakeCamoufoxManager:
        manager = FakeCamoufoxManager(**kwargs)
        calls.append(kwargs)
        return manager

    monkeypatch.setattr(browser_factory, "Camoufox", fake_camoufox)
    profile = tmp_path / "profile"

    session = browser_factory.launch_stealth_browser(profile_dir=profile)

    assert profile.exists()
    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["headless"] is False
    assert kwargs["os"] == "windows"
    assert kwargs["locale"] == "id-ID"
    assert kwargs["humanize"] is True
    assert kwargs["block_webrtc"] is True
    assert kwargs["enable_cache"] is True
    assert kwargs["persistent_context"] is True
    assert Path(str(kwargs["user_data_dir"])) == profile.resolve()
    assert session.context is not None
    assert session.browser is None
    assert session.manager is not None


def test_close_browser_session_closes_context_and_manager(tmp_path: Path, monkeypatch) -> None:
    def fake_camoufox(**kwargs: object) -> FakeCamoufoxManager:
        return FakeCamoufoxManager(**kwargs)

    monkeypatch.setattr(browser_factory, "Camoufox", fake_camoufox)
    session = browser_factory.launch_stealth_browser(profile_dir=tmp_path / "p")

    browser_factory.close_browser_session(session)

    assert session.context.closed is True
    assert session.manager.exited is True
