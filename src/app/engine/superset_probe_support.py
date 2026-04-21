from __future__ import annotations

from typing import Any


def is_sql_lab_candidate(url: str) -> bool:
    normalized = url.lower()
    return "/sqllab/" in normalized or "/api/v1/sqllab/" in normalized


class SupersetNetworkProbe:
    def __init__(self) -> None:
        self._candidates: list[str] = []

    def attach(self, page: Any) -> "SupersetNetworkProbe":
        def capture(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if is_sql_lab_candidate(url):
                self._candidates.append(url)

        page.on("response", capture)
        return self

    def candidate_summaries(self) -> list[str]:
        return list(self._candidates)
