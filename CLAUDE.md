# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`phone-pilot` is a multi-platform mobile automation toolkit exposed as an MCP server. It lets AI agents drive Android (primary; ADB + UIAutomator) and HarmonyOS (via `hmdriver2` + HDC) devices. iOS is planned but unimplemented. Requires Python >= 3.13.

## Commands

```bash
pip install -e ".[dev]"   # dev deps (ruff, pytest); use ".[all]" for harmony+vision extras

make lint                 # ruff check .
make format               # ruff format . (make format-check for CI)
make test                 # python -m pytest
make check                # lint + test

python -m pytest tests/test_verify.py                    # single test file
python -m pytest tests/test_verify.py::test_name         # single test
python scripts/check_verification_env.py                 # check optional deps (cv2/tesseract/adb)
```

Test paths are `tests/` and `phone_pilot/tests/` (configured in `pyproject.toml`).

### Entry points (defined in `pyproject.toml [project.scripts]`)

- `phone-pilot-mcp` → `phone_pilot.cli:main` — starts the MCP stdio server.
- `phone-pilot-call` → `phone_pilot.mcp_call:main` — minimal stdio MCP client for invoking tools from the CLI (`--list-tools`, `<tool_name> --args '<json>'`).
- `phone-pilot-web` → `phone_pilot.web:_cli` — local dashboard (stdlib `http.server`) on `:8686` for browsing run records and `@res:` images.

## Architecture

The codebase is layered. Understanding the flow from MCP tool → driver is key:

```
MCP tools (phone_pilot/mcp/)  ─┐
script_api (phone_pilot/script_api/) ─┤→ core/skills.py (DeviceSkills) → core/protocols.py (Protocols)
                                       │                                   ↓
                                       └→ driver_factory / _helpers ──→ platform driver (android/ | harmony/)
```

- **`core/protocols.py`** — the contract. `DeviceDriver` is the top-level Protocol (structural typing) composed of sub-drivers: `InputDriver`, `ScreenDriver`, `UIDriver`, `AppDriver`. Every platform driver must satisfy these. Platform-agnostic code depends only on these Protocols, never on concrete drivers.
- **`core/skills.py`** — `DeviceSkills` wraps a `DeviceDriver` and combines it with platform-agnostic `extensions/` (vision template matching, OCR, screenshot diff) for higher-level operations like `find_image_on_screen`, `find_text_on_screen`.
- **`core/driver_factory.py`** — `create_driver(serial, platform)` with `"auto"` detection (probes ADB, then HDC). Note: `mcp/_helpers.py` has its own simpler `get_driver` plus `_resolve_device_serial` (scans both ADB and HDC, returns a structured error dict if the device isn't found) — MCP tools resolve serials through `_helpers`, not the factory.
- **`android/`** — `driver.py` is `AndroidDriver`; sub-packages `adb/`, `ui/`, `touch/`, `recording/`, `device/` implement the actual commands. `adb/runner.py` (`CommandRunner`) centralizes subprocess execution.
- **`extensions/`** — platform-independent: `vision/` (OpenCV template + SIFT), `ocr/` (Tesseract), `lang/` (langdetect/translate, optional), `diff/` (screenshot compare), `llm/` (LLM page analysis for self-healing, optional). Optional deps degrade gracefully — missing cv2/tesseract returns an error dict, never crashes.

### MCP module (`phone_pilot/mcp/`)

The server is split by domain. `server.py` creates the single `FastMCP` instance and imports each `_*_tools.py` submodule — **each submodule registers its tools via `@mcp.tool()` decorators on import** (star-imported back into `server.py` for backward-compat so tests can import tool fns from `phone_pilot.mcp.server`).

- `_import_fastmcp()` in `server.py` guards against the local `phone_pilot.mcp` package shadowing the external `mcp` (FastMCP) package on `sys.path` — be careful when editing imports here.
- **Layered tool loading** (`_tool_registry.py`): ~15 `CORE_TOOLS` are always visible; the rest are hidden after startup to cut agent context overhead (~10K→~2.3K tokens) and exposed on demand via the `phone_discover_tools` meta-tool / `activate_category`. Set `PHONE_PILOT_LOAD_ALL_TOOLS=1` to register everything (used by tests). When adding a new tool, also add its name to `CORE_TOOLS` or the appropriate `EXTENDED_CATEGORIES` entry, otherwise it stays hidden in layered mode.
- `tool_contracts.py` holds shared schema/response contract definitions. `_state.py` holds shared mutable state (recording/logcat) guarded by `_state_lock`.
- **Response contract**: every `phone_*` tool returns a `dict`. Success → `{"ok": True, ...}`; failure → `{"ok": False, "error": str, ...}`. Never raise out of a tool. `_with_device` in `_helpers.py` wraps tool bodies to resolve the serial and convert exceptions into this envelope.
- `phone_get_page_state` is the LLM-first tool: one call returns Activity, numbered interactive elements with coordinates, scrollable regions, and all text. With `annotate_elements=True` it overlays element numbers on the screenshot so the agent can call `phone_tap_element(index=N)`.
- **Session recording → script (`_recorder.py` + `_script_record_tools.py`)**: `server.py` wraps `mcp.tool` via `install_recording_hook` *before* tool modules import, so every registered tool reports its calls. `phone_record_script_start` / `phone_record_script_stop` capture whitelisted **action** tools (tap/swipe/input/launch/…; pure queries like `phone_get_page_state` are excluded) and `stop` returns a runnable `script_api` script generated from the `CODEGEN` map (MCP tool name → script_api source line). `phone_replay_trace` re-executes a captured trace. The hook is a no-op unless recording is active, and recording failures never break a tool call. When adding a new action tool that should be recordable, add a `CODEGEN` entry for it.

### script_api (`phone_pilot/script_api/`)

The Python authoring surface for automation scripts (the non-MCP path). `__init__.py` aggregates the public API from submodules — import everything from `phone_pilot.script_api`, not the submodules directly.

- `context.py` — `ScriptContext` carries device/platform plus toggles: `auto_screenshot`, `auto_dump_hprof`, `popup_guard` (default on), `llm_healing`, `max_heal_attempts`.
- `runner.py` — `run_script(main, ctx=...)` is the script entry harness: handles exit codes, tracebacks, and `RunSession` finalization. `chain(ctx)` is the fluent API; `retry()` and `scroll_to_find()` are the retrying helpers. `_finalize_run` stops recording/logcat, writes the summary, and persists healing events.
- `find.py` / `_locate.py` / `_ocr.py` — element location (UIAutomator → OCR → image fallback). `actions.py` — taps/swipes/text/app control. `popup_guard.py` + `self_healer.py` — the self-healing pipeline.
- `logcat.py` — log search/wait helpers: `logcat_find`, `logcat_wait_for`, `logcat_mark` (capture device timestamp), `logcat_since` (read logs after a timestamp via `logcat -T`), `logcat_assert_sequence` (verify patterns appear in order, subsequence match).

#### Robust interaction & waiting (in `actions.py` / `android/ui/wait.py`)

These exist to fix flaky fixed-`sleep` timing and mis-taps that jump out of the app:

- `tap_when_ready(ctx, text=/resource_id=, require_clickable=True)` — poll until the element is clickable/enabled, then tap it; matched element and tap coords come from the same query (no drift). On timeout it does **not** tap.
- `safe_tap(ctx, x, y, expect_activity=/expect_package=)` — tap then verify foreground state; if `package` changed (jumped out of the app) or expectations aren't met, auto `go_back` and re-verify recovery. Default: any package change is treated as a mis-tap.
- `safe_close(ctx, resource_id=/text=, fallback_coord=, max_attempts=)` — full close flow: wait button ready → tap → verify the button disappeared → `go_back` and retry. Falls back to a fixed coordinate (with mis-tap protection) when the button can't be located.
- `wait_for_element_appear(..., require_clickable=, require_enabled=)` — element-present checks can now require interactive state. `wait_for_activity_change(from_activity=None, to_activity=None)` — confirms an actual transition occurred (baseline + leave), unlike `wait_for_activity` which succeeds if already on the target.

#### Discoverability

The public surface is enumerated from `__all__` via `script_api/_introspect.py` (single source of truth):

- `phone-pilot-call --list-script-api` prints every public function with its signature and one-line summary.
- The same `list_public_api()` powers any generated docs, so the list never drifts from code.

#### MCP tool ↔ script_api mapping

MCP tools (for agents) and script_api (for Python scripts) are two surfaces over the same drivers. Rough correspondence:

| MCP tool | script_api equivalent |
|----------|----------------------|
| `phone_tap(x, y)` | `tap_xy(ctx, x, y)` |
| `phone_tap_text(text)` | `find_text(ctx, text).tap()` |
| `phone_tap_element(index)` | (via `phone_get_page_state` numbering; script side uses `find_*().tap()`) |
| `phone_input_text(text)` | `type_text(ctx, text)` |
| `phone_swipe(...)` | `swipe(ctx, ...)` / `swipe_up/down/left/right(ctx)` |
| `phone_find_element(text=/resource_id=)` | `find_text(ctx, text)` / `raw_uia(ctx, selector, by=)` |
| `phone_wait_for_element(..., require_clickable=)` | `tap_when_ready(ctx, ...)` (wait) / `wait_for_element_appear(...)` |
| `phone_scroll_to_find(text)` | `scroll_to_find(ctx, text)` |
| `phone_screenshot()` | `screenshot(ctx, name)` |
| `phone_launch_app(pkg)` / `phone_force_stop(pkg)` | `restart_app_pkg(ctx, pkg)` |
| `phone_go_back()` / `phone_go_home()` | `ctx.driver.go_back()` / `reset_home_screen(ctx)` |
| `phone_search_logcat(pattern)` | `logcat_find(ctx, pattern)` / `logcat_since` / `logcat_assert_sequence` |
| `phone_run_script(path)` | `run_script(main, ctx=...)` (the harness scripts call) |

### Self-healing (3 tiers, when a step fails)

1. **Experience store** (`core/healing_experience.py`) — replays prior successful corrections keyed by `action + query + app_package + activity`; zero-cost, shared across scripts.
2. **PopupGuard** (`script_api/popup_guard.py`) — Window-level + UI-tree + text-rule detection to auto-dismiss popups.
3. **LLM analysis** (`extensions/llm/`, optional) — screenshot + UI tree + error context diagnosis; requires `OPENAI_API_KEY`. Successful LLM fixes are written back to the experience store.

### Run records (`core/run_store.py`, `RunSession`)

Every script run writes a timestamped dir under `.recordings/runs/{script}_{git}_{ts}/` containing the script copy, git info/diff, console log, per-step results + screenshots, recordings, logcat, meminfo, healing events, and an auto-generated `report.html` (`core/html_report.py`, Jinja2 templates in `core/templates/`). `.recordings/` is gitignored. Override roots via `PHONE_PILOT_CACHE_DIR` / `PHONE_PILOT_RECORDINGS_DIR`.

## Conventions

- Three-platform feature parity is tracked in `phone_pilot/FEATURE_MATRIX.md` — **update it when you implement a platform feature** (the file itself states this rule).
- New platform support = implement the `core/protocols.py` Protocols in a new package; wire it into `driver_factory.py` and `mcp/_helpers.py`.
- Comments and docstrings in this repo are frequently bilingual (Chinese/English); match the surrounding style of the file you edit.
- `docs/`, demo scripts under `scripts/`, and resource PNGs are intentionally excluded from git (see `.gitignore`); don't assume they exist when reasoning about CI.

## Environment variables

`PHONE_PILOT_LOAD_ALL_TOOLS` (disable layered tools), `PHONE_PILOT_CACHE_DIR`, `PHONE_PILOT_RECORDINGS_DIR`, `PHONE_PILOT_LOG_LEVEL` (quiet/normal/verbose), `PHONE_PILOT_CMD_DELAY_S` (ADB/HDC inter-command delay, default 0.4), `PHONE_PILOT_DISABLE_TRANSLATION`, `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` (LLM healing).
