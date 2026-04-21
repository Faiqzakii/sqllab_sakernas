from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from app.engine.superset_auth import SupersetAuthBootstrap


def main() -> None:
    root = ROOT
    base_url = "https://fasih-dashboard.bps.go.id"
    sql_lab_url = "https://fasih-dashboard.bps.go.id/superset/sqllab/"

    auth = SupersetAuthBootstrap(base_url=base_url, sql_lab_url=sql_lab_url)
    auth_result = auth.login_and_capture()
    try:
        page = auth_result.page
        print(json.dumps({"current_url": page.url}, ensure_ascii=False, indent=2))

        diagnostics = []
        for attempt in range(10):
            page.wait_for_timeout(1000)
            diagnostics = page.evaluate(
                """
                () => {
                  const allAceEditors = Array.from(document.querySelectorAll('.ace_editor'));
                  const visibleAceEditors = allAceEditors.filter(
                    node => node instanceof HTMLElement && node.offsetParent !== null
                  );
                  const activeElement = document.activeElement;
                  return {
                    allAceCount: allAceEditors.length,
                    visibleAceCount: visibleAceEditors.length,
                    visibleEditors: visibleAceEditors.map((node, index) => {
                      const isFocused = node.contains(activeElement);
                      let value = null;
                      let sessionId = null;
                      let hasCursor = false;
                      try {
                        if (window.ace && typeof window.ace.edit === 'function') {
                          const editor = window.ace.edit(node);
                          if (editor && typeof editor.getValue === 'function') {
                            value = editor.getValue();
                            hasCursor = !!editor.getCursorPosition;
                            if (editor.session && editor.session.id !== undefined) {
                              sessionId = String(editor.session.id);
                            }
                          }
                        }
                      } catch (err) {
                        value = `ACE_ERROR: ${String(err)}`;
                      }
                      return {
                        index,
                        className: node.className,
                        textLength: typeof value === 'string' ? value.length : null,
                        preview: typeof value === 'string' ? value.slice(0, 200) : null,
                        isFocused,
                        sessionId,
                        hasCursor,
                      };
                    }),
                  };
                }
                """
            )
            print(json.dumps({"attempt": attempt + 1, **diagnostics}, ensure_ascii=False, indent=2))
            if diagnostics.get("visibleAceCount", 0) > 0:
                break
    finally:
        auth_result.close()


if __name__ == "__main__":
    main()
