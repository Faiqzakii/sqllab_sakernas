from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
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
    )

    try:
        time.sleep(3)
        dataset_path = str(root / "data" / "superset_data.json")
        payload = {
            "name": "fanout-live-superset",
            "execution_mode": "superset_sql",
            "sql_template": (
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
                "WHERE art.level_2_code='{{ level_2_code }}'"
            ),
            "params_schema_json": {
                "base_url": "https://fasih-dashboard.bps.go.id",
                "sql_lab_url": "https://fasih-dashboard.bps.go.id/superset/sqllab/",
                "source_data_path": dataset_path,
                "batching_strategy": {
                    "type": "explicit_list",
                    "param": "level_2_code",
                    "values": ["01", "02", "03", "04", "71"],
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
            print("POST_JOB_STATUS=" + str(response.status))
            print("JOB_ID=" + str(job["id"]))

        execute_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/job-definitions/{job['id']}/execute?debug=true",
            data=b"",
            method="POST",
        )
        execution_result: dict[str, object] = {}
        execution_error: dict[str, object] = {}

        def run_execute_request() -> None:
            try:
                with urllib.request.urlopen(execute_request, timeout=900) as response:
                    execution = json.loads(response.read().decode("utf-8"))
                    execution_result["status"] = response.status
                    execution_result["body"] = execution
            except urllib.error.HTTPError as exc:
                execution_error["status"] = exc.code
                execution_error["body"] = exc.read().decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                execution_error["status"] = "exception"
                execution_error["body"] = repr(exc)

        execute_thread = threading.Thread(target=run_execute_request, daemon=True)
        execute_thread.start()

        batch_debug_path = root / "artifacts" / "snapshots" / "0" / "batches.json"
        batch_log_path = root / "artifacts" / "snapshots" / "0" / "batches.log.jsonl"
        last_batch_count = -1
        start_time = time.time()
        while execute_thread.is_alive() and (time.time() - start_time) < 900:
            snapshot_root = root / "artifacts" / "snapshots"
            batch_files = sorted(snapshot_root.glob("*/batches.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if batch_files:
                batch_debug_path = batch_files[0]
                batch_log_path = batch_debug_path.with_name("batches.log.jsonl")
                try:
                    batch_debug = json.loads(batch_debug_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    batch_debug = []
                if len(batch_debug) != last_batch_count:
                    last_batch_count = len(batch_debug)
                    print("BATCH_PROGRESS_FILE=" + str(batch_debug_path))
                    print("BATCH_PROGRESS_COUNT=" + str(last_batch_count))
                    print("BATCH_PROGRESS_JSON=" + json.dumps(batch_debug, ensure_ascii=False))
            if batch_log_path.exists():
                try:
                    log_lines = batch_log_path.read_text(encoding="utf-8").splitlines()
                    if log_lines:
                        print("BATCH_LOG_LAST=" + log_lines[-1])
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(5)

        execute_thread.join(timeout=1)
        try:
            if execution_error:
                print("POST_EXECUTE_STATUS=" + str(execution_error["status"]))
                print("POST_EXECUTE_BODY=" + str(execution_error["body"]))
                raise RuntimeError(str(execution_error["body"]))

            execution = execution_result["body"]
            print("POST_EXECUTE_STATUS=" + str(execution_result["status"]))
            print("EXECUTION=" + json.dumps(execution))
        except Exception:
            if proc.stdout is not None:
                time.sleep(1)
                print("SERVER_LOG_START")
                log_output = proc.stdout.readline()
                while log_output:
                    print(log_output.rstrip())
                    log_output = proc.stdout.readline()
                print("SERVER_LOG_END")
            raise

        artifact_path = root / execution["artifact_path"]
        rows = json.loads(artifact_path.read_text(encoding="utf-8"))
        batch_debug = json.loads(batch_debug_path.read_text(encoding="utf-8"))
        print("ARTIFACT_EXISTS=" + str(artifact_path.exists()))
        print("ARTIFACT_ROW_COUNT=" + str(len(rows)))
        if rows:
            print("ARTIFACT_FIRST_IDENTITY=" + str(rows[0].get("identity_key")))
        print("BATCH_DEBUG_EXISTS=" + str(batch_debug_path.exists()))
        print("BATCH_DEBUG_COUNT=" + str(len(batch_debug)))
        print("BATCH_DEBUG_JSON=" + json.dumps(batch_debug, ensure_ascii=False))
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
