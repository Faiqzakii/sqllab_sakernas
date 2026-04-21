from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    root = Path(__file__).resolve().parent
    port = pick_free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            "src",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        time.sleep(3)
        dataset_path = str(root / "data" / "superset_data.json")
        payload = {
            "name": "single-batch-live-superset",
            "execution_mode": "superset_sql",
            "sql_template": (
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
                "WHERE art.level_2_code='{{ level_2_code }}'"
            ),
            "params_schema_json": {
                "base_url": "https://fasih-dashboard.bps.go.id",
                "sql_lab_url": "https://fasih-dashboard.bps.go.id/superset/sqllab/",
                "source_data_path": dataset_path,
                "batching_strategy": {
                    "type": "explicit_list",
                    "param": "level_2_code",
                    "values": ["01"],
                },
            },
            "merge_key_columns_json": ["identity_key"],
            "identity_columns_json": [
                "identity_key",
                "KODE_PROV",
                "KODE_KAB",
                "KODE_KEC",
                "KODE_DESA",
                "SLS",
                "SUBSLS",
                "NKS",
                "DSRT",
                "NO_ART",
            ],
        }

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/job-definitions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            job = json.loads(response.read().decode("utf-8"))
            print("POST_JOB_STATUS=" + str(response.status), flush=True)
            print("JOB_ID=" + str(job["id"]), flush=True)

        execute_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/job-definitions/{job['id']}/execute?debug=true",
            data=b"",
            method="POST",
        )
        try:
            with urllib.request.urlopen(execute_request, timeout=180) as response:
                execution = json.loads(response.read().decode("utf-8"))
                print("POST_EXECUTE_STATUS=" + str(response.status), flush=True)
                print("EXECUTION=" + json.dumps(execution), flush=True)
        except urllib.error.HTTPError as exc:
            print("POST_EXECUTE_STATUS=" + str(exc.code), flush=True)
            print("POST_EXECUTE_BODY=" + exc.read().decode("utf-8", errors="replace"), flush=True)
            raise

        artifact_path = root / execution["artifact_path"]
        rows = json.loads(artifact_path.read_text(encoding="utf-8"))
        batch_debug_path = root / execution["batch_debug_path"]
        batch_debug = json.loads(batch_debug_path.read_text(encoding="utf-8"))
        print("ARTIFACT_EXISTS=" + str(artifact_path.exists()), flush=True)
        print("ARTIFACT_ROW_COUNT=" + str(len(rows)), flush=True)
        if rows:
            print("ARTIFACT_FIRST_IDENTITY=" + str(rows[0].get("identity_key")), flush=True)
        print("BATCH_DEBUG_EXISTS=" + str(batch_debug_path.exists()), flush=True)
        print("BATCH_DEBUG_COUNT=" + str(len(batch_debug)), flush=True)
        print("BATCH_DEBUG_JSON=" + json.dumps(batch_debug, ensure_ascii=False), flush=True)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


if __name__ == "__main__":
    main()
