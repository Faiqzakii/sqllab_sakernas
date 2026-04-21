from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from app.engine.superset_auth import SupersetAuthBootstrap
from app.engine.superset_ui_runner import click_run_query, fill_sql_editor


SQL = (
    "SELECT art.level_1_code AS KODE_PROV, art.level_1_name AS PROV, "
    "art.level_2_code AS KODE_KAB, art.level_2_name AS KAB, "
    "art.level_3_code AS KODE_KEC, art.level_3_name AS KEC, "
    "art.level_4_code AS KODE_DESA, art.level_4_name AS DESA, "
    "art.level_5_code AS SLS, art.level_6_code AS SUBSLS, root.nks AS NKS, "
    "root.no_dsrt AS DSRT, art.ppno AS NO_ART, art.dem_name AS NAMA_ART, art.*, root.*, base.*, "
    "'LINK' AS Link, root.survey_period_id "
    "FROM tmx_1e42622b.art_roster art "
    "LEFT JOIN tmx_1e42622b.root_table root ON root.assignment_id = art.assignment_id "
    "LEFT JOIN tmx_1e42622b.base_table_assignment base ON base.id = art.assignment_id "
    "WHERE art.level_2_code='01'"
)


def main() -> None:
    auth = SupersetAuthBootstrap(
        base_url="https://fasih-dashboard.bps.go.id",
        sql_lab_url="https://fasih-dashboard.bps.go.id/superset/sqllab/",
    )
    auth_result = auth.login_and_capture()
    try:
        page = auth_result.page
        captured: list[dict[str, object]] = []

        def capture_request(request) -> None:
            url = getattr(request, "url", "")
            if "/api/v1/sqllab/execute/" not in str(url):
                return
            try:
                post_data = request.post_data
            except Exception as exc:  # noqa: BLE001
                post_data = f"POST_DATA_ERROR: {exc}"
            captured.append(
                {
                    "url": str(url),
                    "method": getattr(request, "method", None),
                    "resource_type": getattr(request, "resource_type", None),
                    "post_data": post_data,
                }
            )

        page.on("request", capture_request)
        fill_sql_editor(page, SQL)
        click_run_query(page)
        for _ in range(10):
            page.wait_for_timeout(1000)
            if captured:
                break

        print(json.dumps({"captured_requests": captured, "current_url": page.url}, ensure_ascii=False, indent=2))
    finally:
        auth_result.close()


if __name__ == "__main__":
    main()
