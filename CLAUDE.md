# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ACADxPDF is a bidirectional batch converter between AutoCAD DWG and PDF, with automatic drawing border (图框) detection. A single DWG may contain multiple drawing sheets; the tool detects each border and outputs separate PDFs. It also supports PDF→DWG reverse conversion via AutoCAD PDFIMPORT. Targets the Chinese AEC workflow (supports 天正/TArch plugin). Runs on **Windows native** and **WSL2** with AutoCAD 2026.

## Commands

```bash
# Activate conda environment (required first)
conda activate pdf

# Start the Flask API server (Web UI at http://localhost:5557)
python -m acad2pdf.api

# Start a remote worker process (reads workers.json)
python -m acad2pdf.worker

# CLI — single file or batch conversion
python -m acad2pdf.converter <input_path> [-o output_dir] [--no-split] [--no-auto-size]

# Batch convert via Python API
python -c "from acad2pdf import batch_convert; batch_convert('input', 'output', split_borders=True, auto_paper_size=True)"

# Integration test
python tools/test_api.py

# DXF analysis/debugging
python tools/analyze_dxf.py

# Multi-thread benchmark (CLI, writes report to docs/)
python tools/bench_threads.py

# API-based benchmark
python tools/benchmark_api.py
```

No test suite, linter, formatter, or CI/CD. No `setup.py`/`pyproject.toml`/`requirements.txt` — dependencies installed manually into conda env `pdf`.

## Architecture

```
acad2pdf/
├── converter.py        — Core engine: border detection, LSP generation, DWG↔PDF conversion
├── api.py              — Flask app: Web UI, SSE, file upload, task management, SSO
├── task_store.py       — Unified in-memory task queue + worker registry (TaskStore)
├── worker.py           — Pull-based worker: local threads or remote process
├── dispatch_routes.py  — Flask Blueprint: /dispatch/* endpoints for worker pull protocol
├── pdf2dwg_api.py      — Flask Blueprint: PDF→DWG conversion endpoints
├── pdf2dwg_worker.py   — PDF→DWG via acad.exe + PDFIMPORT AutoLISP
└── dispatcher.py       — DEPRECATED (logic moved to task_store + dispatch_routes)

lsp/                     — AutoLISP modules loaded inside acad.exe
├── autoplot.lsp         — Entry point: loads modules, registers c:AutoPlot / c:BatchPlot
├── ap-detect.lsp        — Border detection: block name matching + LWPOLYLINE rectangles
├── ap-plot.lsp          — Print output: ActiveX PlotToFile + -PLOT command fallback
├── ap-paper.lsp         — Paper matching: PC3 paper table + scale auto-detection
├── ap-batch.lsp         — Batch directory processing with progress save/resume
├── ap-config.lsp        — Configuration loading from .env-style files
├── ap-utils.lsp         — Error handling, sysvar save/restore, geometry helpers
└── autoplot.env         — Example LSP config file
```

### Two Conversion Paths

1. **LSP path** (primary, used by API/worker): `convert_dwg_lsp()` launches `acad.exe`, loads `autoplot.lsp` which detects borders, matches paper sizes, and plots each frame to PDF via ActiveX `PlotToFile`. Supports TArch plugin via ARX loading.

2. **Python path** (legacy CLI): `convert_dwg()` uses `accoreconsole.exe` to DWG→DXF, then `ezdxf` parses borders, generates a `-PLOT` script, and runs accoreconsole again. No TArch support.

### Data Flow (DWG→PDF via LSP — primary)

1. User uploads DWG via `POST /convert` → `api.py` creates `Task` in `TaskStore`
2. Local `Worker` threads pull `FileItem`s via `TaskStore.pull()`
3. Worker calls `convert_dwg_lsp()` which copies DWG to `_work/<uuid>/` with ASCII-safe name, generates `autoplot.env` + startup script, launches `acad.exe /ld tch_kernal.arx <dwg> /b <script>`
4. Inside AutoCAD: `autoplot.lsp` → `ap-detect` finds borders → `ap-paper` matches paper → `ap-plot` outputs PDFs → writes manifest
5. Worker reports result (multipart upload with PDFs/DXFs) → `dispatch_routes._finalize_task()` creates ZIP, broadcasts SSE
6. User downloads ZIP via `GET /download/<task_id>`

### Key Classes

- **`Border`** — detected border with world/local bbox, scale, paper size. Properties: `paper_width_mm`, `paper_height_mm`, `orientation`, `size_label`, `paper_size_name`
- **`ConversionResult`** — success/failure, output paths, border list, elapsed time
- **`Task`** / **`FileItem`** — task model with per-file status tracking (pending→assigned→done/failed)
- **`WorkerInfo`** — worker registry entry with heartbeat, capacity, stats
- **`TaskStore`** — singleton in-memory store with thread-safe queue, worker registry, stale task recovery

### Border Detection (both Python and LSP)

1. **Block detection** — scans INSERT blocks whose short side matches A3+ paper sizes at common scales. Block names matched against `BORDER_KEYWORDS`. LSP version also checks dynamic block `EffectiveName` and scans paper space.
2. **Rectangle detection** — fallback: finds closed LWPOLYLINE rectangles and LINE-covered bounding boxes, filters by standard paper sizes, removes contained rectangles.

`merge_nearby_borders()` uses union-find to group adjacent borders when merge mode is on.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | JSON status |
| `/convert` | POST | Upload DWGs, creates task, starts local worker |
| `/stream` | GET | SSE real-time progress |
| `/task/<id>` | GET | Task status |
| `/download/<id>` | GET | Download result ZIP + cleanup |
| `/tasks` | GET | List all tasks |
| `/config` | GET/POST | Runtime config (printer, timeout, keywords, etc.) |
| `/plot-styles` | GET/POST/DELETE | CTB plot style management |
| `/admin/config` | GET/POST | Admin config (requires auth) |
| `/admin/workers` | GET | Worker status |
| `/admin/clean` | POST | Clean `_work/` and `output/` |
| `/logs` | GET | Recent log lines |
| `/health` | GET | Health check |
| `/dispatch/register` | POST | Worker registration |
| `/dispatch/heartbeat` | POST | Worker heartbeat |
| `/dispatch/pull` | POST | Pull tasks (capacity capped at 16) |
| `/dispatch/result` | POST | Upload conversion results |
| `/dispatch/file/<id>` | GET | Download source file |
| `/convert-pdf` | POST | PDF→DWG batch upload |
| `/convert-pdf/add/<id>` | POST | Add files to running task |
| `/download-pdf-zip/<id>` | GET | Download PDF→DWG result ZIP |
| `/callback` | GET | SSO ticket callback |
| `/auth/check` | GET | Login status |

## Configuration

All config in `.env`. Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ACAD_PATH` | `C:\opt\AutoCAD 2026\accoreconsole.exe` | accoreconsole for Python path |
| `ACAD_EXE` | `C:\opt\AutoCAD 2026\acad.exe` | Full AutoCAD for LSP path |
| `TARCH_ARX` | — | 天正 plugin ARX path (loaded by acad.exe) |
| `ACAD_TEMPLATE` | — | Template DWT for acad.exe sessions |
| `WORK_DIR` | (empty → `_work/`) | Work directory |
| `PRINTER` | `DWG To PDF.pc3` | PC3 printer config |
| `PLOT_STYLE` | `monochrome.ctb` | Plot style table |
| `TIMEOUT` | `180` | Per-file timeout (seconds) |
| `BORDER_KEYWORDS` | `TK,TUKUANG,BORDER,FRAME,TITLE` | Block name keywords |
| `MAX_WORKERS` | `6` | Max local worker threads |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `5557` | Flask binding |
| `API_KEY` | auto-generated | Worker authentication key |
| `SSO_URL` | — | SSO server URL (optional) |
| `LOG_MAX_BYTES` | `20971520` | Log rotation size |
| `LOG_BACKUP_COUNT` | `5` | Rotated log files |

`workers.json` configures remote worker nodes (name, URL, capacity). `plot_styles/` contains `DWG To PDF.pmp` (JSON-format PMP with 192 custom paper sizes) and `monochrome.ctb`.

## Dependencies

- **Python 3.10+** (conda env: `pdf`)
- **ezdxf** — DXF file parsing (Python path only)
- **flask** — REST API + Web UI + SSE
- **pikepdf** — PDF split/merge
- **AutoCAD 2026** — `accoreconsole.exe` + `acad.exe`

## Platform Support

`converter.py` auto-detects via `_is_wsl()`:
- **Windows native**: Paths as-is. accoreconsole/acad invoked directly.
- **WSL2**: `/mnt/c/...` → `C:\...` via `_to_native_path()`. Work dirs must be on Windows filesystem.

## Language

Primary language is Chinese (README, docs, code comments, docstrings). Variable/function names are English.
