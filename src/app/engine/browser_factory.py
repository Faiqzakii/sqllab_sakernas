from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from camoufox.sync_api import Camoufox

DEFAULT_PROFILE_DIR = Path(".camoufox-profile")
PROFILE_ENV_KEY = "SUPERSET_BROWSER_PROFILE_DIR"


@dataclass
class BrowserSession:
    manager: Any
    context: Any
    browser: Any | None = None


def resolve_profile_dir(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env_value = os.environ.get(PROFILE_ENV_KEY)
    if env_value:
        return Path(env_value).expanduser().resolve()
    return DEFAULT_PROFILE_DIR.expanduser().resolve()


def launch_stealth_browser(
    *,
    profile_dir: str | Path | None = None,
    headless: bool = False,
) -> BrowserSession:
    resolved_profile = resolve_profile_dir(profile_dir)
    resolved_profile.mkdir(parents=True, exist_ok=True)

    manager = Camoufox(
        headless=headless,
        os="windows",
        locale="id-ID",
        humanize=True,
        block_webrtc=True,
        enable_cache=True,
        persistent_context=True,
        user_data_dir=str(resolved_profile),
    )
    context = manager.__enter__()
    return BrowserSession(manager=manager, context=context, browser=None)


def close_browser_session(session: BrowserSession) -> None:
    errors: list[Exception] = []
    if session.context is not None:
        try:
            session.context.close()
        except Exception as exc:  # noqa: BLE001 - aggregate cleanup
            errors.append(exc)
    if session.browser is not None:
        try:
            session.browser.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    if session.manager is not None:
        try:
            session.manager.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    if errors:
        raise RuntimeError(f"Browser session cleanup failed: {errors}")
