from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from app.engine.superset_auth import SupersetAuthBootstrap
from app.engine.superset_client import SupersetClient
from app.engine.superset_ui_runner import SupersetUiRunner


SQL_TEMPLATE = (
    "SELECT art.level_1_code AS KODE_PROV, art.level_1_name AS PROV, "
    "art.level_2_code AS KODE_KAB, art.level_2_name AS KAB, "
    "art.level_3_code AS KODE_KEC, art.level_3_name AS KEC, "
    "art.level_4_code AS KODE_DESA, art.level_4_name AS DESA, "
    "art.level_5_code AS SLS, art.level_6_code AS SUBSLS, root.nks AS NKS, "
    "root.no_dsrt AS DSRT, art.ppno AS NO_ART, art.dem_name AS NAMA_ART, art.*, root.*, base.*, "
    "CONCAT('<a href=\"https://fasih-sm.bps.go.id/survey-collection/assignment-detail/', art.assignment_id, '/9b637b4c-2839-4a16-9023-1a62c364572b\" target=\"_blank\">Link Assignment</a>') AS Link, root.survey_period_id "
    "FROM tmx_1e42622b.art_roster art "
    "LEFT JOIN tmx_1e42622b.root_table root ON root.assignment_id = art.assignment_id "
    "LEFT JOIN tmx_1e42622b.base_table_assignment base ON base.id = art.assignment_id "
    "WHERE art.level_2_code='{level_2_code}'"
)


def main() -> None:
    auth = SupersetAuthBootstrap(
        base_url="https://fasih-dashboard.bps.go.id",
        sql_lab_url="https://fasih-dashboard.bps.go.id/superset/sqllab/",
    )
    auth_result = auth.login_and_capture()
    try:
        session = auth.build_requests_session(auth_result.cookies)

        def debug_callback(event: dict[str, object]) -> None:
            print("BATCH_STAGE=" + json.dumps(event, ensure_ascii=False), flush=True)

        summary: list[dict[str, object]] = []
        for level_2_code in ["01", "02", "03", "04", "71"]:
            sql = SQL_TEMPLATE.format(level_2_code=level_2_code)
            print(f"BATCH_START={level_2_code}", flush=True)
            try:
                ui_runner = SupersetUiRunner(
                    sql_lab_url="https://fasih-dashboard.bps.go.id/superset/sqllab/",
                    auth_cookies=auth_result.cookies,
                    browser=auth_result.browser,
                    context=auth_result.context,
                    page=None,
                    debug_callback=debug_callback,
                )
                client = SupersetClient(
                    session=session,
                    base_url="https://fasih-dashboard.bps.go.id",
                    ui_runner=ui_runner,
                )
                result = client.run_query(sql)
                row_count = len(result.dataframe.index)
                first_kab = None
                if row_count:
                    first_row = result.dataframe.to_dict(orient="records")[0]
                    first_kab = first_row.get("KODE_KAB")
                payload = {
                    "level_2_code": level_2_code,
                    "source": result.source,
                    "row_count": row_count,
                    "first_kab": first_kab,
                }
                summary.append(payload)
                print("BATCH_RESULT=" + json.dumps(payload, ensure_ascii=False), flush=True)
            except Exception as exc:  # noqa: BLE001
                payload = {
                    "level_2_code": level_2_code,
                    "error": repr(exc),
                }
                summary.append(payload)
                print("BATCH_ERROR=" + json.dumps(payload, ensure_ascii=False), flush=True)

        print("FINAL_SUMMARY=" + json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    finally:
        auth_result.close()


if __name__ == "__main__":
    main()
