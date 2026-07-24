# Interactive SQL Runner Design

## One-liner

Add a zero-argument terminal wizard to `scripts/run_sql_folder.py` so users
can run or resume SQL files and folders without remembering CLI flags.

## User Story

As a SQL Lab operator, I want a numbered terminal menu with safe defaults and
automatic resume discovery, so that I can run recurring jobs without
reconstructing long command lines.

## Acceptance Criteria

- Given no arguments, the script displays numbered choices for SQL file, SQL
  folder, latest resumable run, and exit.
- An interactive file run uses UI mode, automatic login, cursor
  `assignment_id`, row cap 1000, and sleep 0.8 seconds without prompting for
  those values.
- An interactive folder run uses UI mode, automatic login, resume enabled,
  retry count 2, and sleep 0.8 seconds.
- New output paths follow
  `artifacts/runs/YYYY-MM-DD_HHMMSS_<sanitized-target-name>`.
- Selecting a target searches compatible `progress.json` files and offers
  resume or start fresh.
- Resume reuses the prior output directory and cursor without requiring
  `--start-after`.
- Start fresh creates a timestamped output directory without overwriting prior
  pages.
- Latest resume identifies source SQL, output directory, page, and cursor,
  then resumes it.
- If no resumable run exists, the wizard prints an Indonesian message and
  returns to its main menu.
- Invalid menu input or a missing path prints an Indonesian error and prompts
  again.
- Existing explicit CLI arguments retain their current noninteractive
  behavior.

## Phased Delivery

### Phase 1: Wizard model and discovery

- Goal: deterministic target selection, output naming, and resume discovery.
- Files: `scripts/run_sql_folder.py`,
  `tests/test_run_sql_folder_interactive.py`.
- Done: unit tests prove naming, target-aware discovery, and menu decisions.

### Phase 2: Interactive execution routing

- Goal: translate wizard choices into existing `argparse.Namespace` execution
  paths without duplicating runners.
- Files: `scripts/run_sql_folder.py`,
  `tests/test_run_sql_folder_interactive.py`, and
  `tests/test_run_sql_folder_paginate.py`.
- Done: file, folder, resume, fresh, invalid-input, and explicit-argument tests
  pass.
- Handoff: consumes discovery and selection helpers from Phase 1.

## Edge Cases / Error States

- Empty `artifacts/runs`: resume reports no run and returns to main menu.
- Multiple runs for one target: newest incomplete compatible progress wins;
  user can still start fresh.
- Complete progress is excluded from resume candidates.
- Corrupt `progress.json` is ignored without crashing.
- Progress missing source identity is excluded from target-aware and latest
  resume.
- Missing path causes another prompt.
- Non-`.sql` file is rejected with an Indonesian error.
- Folder without `.sql` files is rejected before browser launch.
- Noninteractive stdin with no arguments prints help and exits with code 2.
- Timestamp collision gets a numeric suffix instead of overwriting.

## Out of Scope

- Rich/curses UI or third-party prompt dependency.
- Editing SQL inside the wizard.
- Interactive advanced network, retry, page-size, cursor, or browser flags.
- Recovering legacy runs that contain only page SQL files.
- Removing CLI flags used by automation.

## Dependencies

- Python standard library only for prompts, timestamps, paths, and JSON.
- Existing `SupersetQueryRunner`, `run_folder_mode`, `run_paginate_mode`, and
  incremental `progress.json` persistence.
- Existing Camoufox profile and automatic-login behavior.

## Data and Component Design

- `build_default_output_dir(target: Path, now: datetime | None = None) -> Path`
  generates collision-safe timestamped paths.
- `find_resume_candidates(runs_dir: Path, target: Path | None = None)` reads
  incomplete progress files newest first and returns `ResumeCandidate` items.
- New pagination progress stores `source_path` and `source_kind` for
  target-aware discovery.
- `run_interactive_menu() -> int` owns prompts, builds an
  `argparse.Namespace`, then delegates to existing execution functions.
- Prompt I/O is injectable through `input_fn` and `output_fn` for tests.
- Explicit CLI parsing remains the only path when any argument is supplied.

## File Impact Estimate

### Files modified

- `scripts/run_sql_folder.py`: menu, naming, source identity, discovery, and
  zero-argument routing.
- `tests/test_run_sql_folder_paginate.py`: source identity persistence and
  resume compatibility.

### Files created

- `tests/test_run_sql_folder_interactive.py`: wizard, invalid input, naming,
  discovery, and CLI compatibility tests.
