# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ACADxPDF is a Python batch DWG-to-PDF converter with automatic drawing border (图框) detection. A single DWG may contain multiple drawing sheets; this tool detects each border and outputs separate PDFs. It targets the Chinese AEC workflow and runs in WSL2 (requires AutoCAD 2022 `accoreconsole.exe` on Windows filesystem).

## Project Location

- **Code & source files**: `/mnt/c/opt/ACADxPDF/` (Windows NTFS, accoreconsole can access directly)
- **AutoCAD 2022**: `/mnt/c/opt/AutoCAD 2022/` (Windows filesystem)

## Commands

```bash
# Activate conda environment (required first)
conda activate pdf

# Start the Flask API server (serves Web UI at http://localhost:5557)
python -m acad2pdf.api

# CLI — single file or batch conversion
python -m acad2pdf.converter <input_path> [-o output_dir] [--no-split] [--no-auto-size]

# Batch convert via Python API
python -c "from acad2pdf import batch_convert; batch_convert('input', 'output', split_borders=True, auto_paper_size=True)"

# Integration test
python tools/test_api.py

# DXF analysis/debugging tool
python tools/analyze_dxf.py

# Multi-thread benchmark
python tools/bench_threads.py
```

No test suite, linter, formatter, or CI/CD is configured. There is no `setup.py`, `pyproject.toml`, or `requirements.txt` — dependencies (ezdxf, flask) are installed manually.

## Architecture

```
acad2pdf/
├── converter.py (969 lines — core engine)
│   ├─ Border detection (block + rectangle strategies)
│   ├─ AutoLISP script generation for -PLOT commands
│   ├─ accoreconsole invocation (DWG→DXF, DXF→PDF)
│   └─ batch_convert() with ThreadPoolExecutor
└── api.py (386 lines — Flask HTTP + Web UI + SSE)
    ├─ Serves static/index.html as Web UI
    ├─ SSE streaming progress via /stream
    └─ ThreadPoolExecutor for concurrent conversions
```

### Data Flow

1. `convert_dwg()` receives a DWG path
2. DWG is copied to a unique `_work/<uuid>/` directory (avoids conflicts under multi-threading)
3. `dwg_to_dxf()` converts DWG→DXF via `accoreconsole.exe` + AutoLISP script
4. `detect_borders()` tries block-name detection first, falls back to closed-rectangle detection
5. All borders are combined into a **single** AutoLISP script (multiple `-PLOT` commands), run by **one** accoreconsole invocation
6. Final PDFs move from `_work/` to output dir, temp dir is cleaned up
7. Returns `ConversionResult` dataclass with paths, border info, timing, status

### Key Classes

- **`Border`** — detected drawing border with world/local bbox, scale, paper size. Properties: `paper_width_mm`, `paper_height_mm`, `orientation`, `size_label`
- **`ConversionResult`** — success/failure, output paths, border list, elapsed time

### Border Detection Strategies

1. **Block detection** (`detect_block_borders`) — scans INSERT blocks whose short side matches A3+ paper sizes (≥280mm). Block names are matched against `BORDER_KEYWORDS` from `.env`.
2. **Rectangle detection** (`detect_rect_borders`) — fallback: finds closed LWPOLYLINE rectangles and LINE-covered bounding boxes, filters by standard paper sizes, removes contained rectangles.

`merge_nearby_borders()` uses a union-find algorithm to group adjacent borders when merge mode is requested.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Web UI (index.html) |
| `/convert` | POST | Multipart upload (`files`), optional `merge=true/false`, returns JSON results |
| `/stream` | GET | SSE real-time progress |
| `/task/<task_id>` | GET | Task status |
| `/download/<task_id>` | GET | Download result ZIP |
| `/tasks` | GET | List all tasks |
| `/config` | GET/POST | Get or update runtime config |
| `/logs` | GET | Recent log lines |
| `/health` | GET | Health check `{"status": "ok"}` |

## Configuration

All config is in `.env` (see `.env.example`). Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ACAD_PATH` | `C:\Autodesk\AutoCAD 2020\accoreconsole.exe` | Path to AutoCAD console |
| `WORK_DIR` | (empty → `_work/`) | Work directory on Windows filesystem |
| `ACAD_UNIT` | `毫米` | Unit name ("毫米" for Chinese, "MM" for English) |
| `PRINTER` | `DWG To PDF.pc3` | PC3 printer config |
| `PLOT_STYLE` | `monochrome.ctb` | Plot style table |
| `TIMEOUT` | `180` | Per-file timeout (seconds) |
| `BORDER_KEYWORDS` | `TK,TUKUANG,BORDER,FRAME,TITLE` | Block name keywords for border detection |
| `MAX_WORKERS` | `6` | Max thread pool workers (recommended 4-8) |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `5557` | Flask server binding |
| `LOG_MAX_BYTES` | `20971520` (20MB) | Log file rotation size |
| `LOG_BACKUP_COUNT` | `5` | Number of rotated log files to keep |

## Dependencies

- **Python 3.10+** (conda env: `pdf` at `/opt/conda3`)
- **ezdxf** — DXF file parsing
- **flask** — REST API + Web UI
- **AutoCAD 2022** — `accoreconsole.exe` at `/mnt/c/opt/AutoCAD 2022/`

## WSL Path Handling

`converter.py` auto-detects WSL via `/proc/version` and converts paths: `/mnt/c/...` → `C:\...`. All accoreconsole calls use Windows paths; all Python I/O uses WSL paths. Work directories must be on Windows filesystem (`/mnt/c/...`) so accoreconsole can access them.

## Language

Primary language is Chinese (README, docs, API docs, code comments, docstrings). Variable/function names are in English.
