# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ACADxPDF is a Python batch DWG-to-PDF converter with automatic drawing border (图框) detection. A single DWG may contain multiple drawing sheets; this tool detects each border and outputs separate PDFs. It targets the Chinese AEC workflow and runs in WSL2 (requires AutoCAD 2022 `accoreconsole.exe` on Windows filesystem).

## Project Location

- **Code & source files**: `/mnt/c/opt/ACADxPDF/` (Windows NTFS, accoreconsole 可直接访问)
- **AutoCAD 2022**: `/mnt/c/opt/AutoCAD 2022/` (Windows filesystem)

## Commands

```bash
# Activate conda environment (required first)
conda activate pdf

# Start the Flask API server
python -m acad2pdf.api

# CLI — single file or batch conversion
python -m acad2pdf.converter <input_path> [-o output_dir] [--no-split] [--no-auto-size]

# Run integration test
python tools/test_api.py

# DXF analysis/debugging tool
python tools/analyze_dxf.py

# Batch convert via Python API
python -c "from acad2pdf import batch_convert; batch_convert('input', 'output', split_borders=True, auto_paper_size=True)"
```

No test suite, linter, formatter, or CI/CD is configured. There is no `setup.py`, `pyproject.toml`, or `requirements.txt` — dependencies (ezdxf, flask) are installed manually.

## Architecture

```
api.py (Flask HTTP layer: /convert, /health)
  └─ imports converter.py (core engine)
       ├─ ezdxf          — DXF parsing for border detection
       ├─ accoreconsole  — headless AutoCAD for DWG→DXF and DWG→PDF
       └─ .env           — runtime configuration
```

### Data Flow

1. `convert_dwg()` receives a DWG path
2. `dwg_to_dxf()` converts DWG→DXF via `accoreconsole.exe` + AutoLISP script
3. `detect_borders()` tries block-name detection first, falls back to closed-rectangle detection
4. For each border, `generate_plot_script()` builds an AutoLISP `-PLOT` script with Window coordinates
5. `run_conversion()` invokes `accoreconsole.exe` with the script to produce PDF
6. Returns `ConversionResult` dataclass with paths, border info, timing, status

### Key Classes

- **`Border`** — detected drawing border with world/local bbox, scale, paper size
- **`ConversionResult`** — success/failure, output paths, border list, elapsed time

### Border Detection Strategies

1. **Block detection** (`detect_block_borders`) — scans INSERT blocks whose short side matches A3+ paper sizes (≥280mm). Block names are matched against `BORDER_KEYWORDS` from `.env`.
2. **Rectangle detection** (`detect_rect_borders`) — fallback: finds closed LWPOLYLINE rectangles and LINE-covered bounding boxes, filters by standard paper sizes, removes contained rectangles.

`merge_nearby_borders()` uses a union-find algorithm to group adjacent borders when merge mode is requested.

## Configuration

All config is in `.env` (see `.env.example`). Key variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ACAD_PATH` | `/mnt/c/opt/AutoCAD 2022/accoreconsole.exe` | Path to AutoCAD console (Windows filesystem) |
| `WORK_DIR` | (空) | 留空则使用 output 目录；设置则在指定目录创建独立子目录 |
| `PRINTER` | `DWG To PDF.pc3` | PC3 printer config |
| `PLOT_STYLE` | `monochrome.ctb` | Plot style table |
| `TIMEOUT` | `180` | Per-file timeout (seconds) |
| `BORDER_KEYWORDS` | `TK,TUKUANG,BORDER,FRAME,TITLE` | Block name keywords for border detection |
| `MAX_WORKERS` | `4` | Max thread pool workers (推荐 2) |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `5557` | Flask server binding |

## Dependencies

- **Python 3.10+** (conda env: `pdf` at `/opt/conda3`)
- **ezdxf** — DXF file parsing
- **flask** — REST API
- **AutoCAD 2022** — `accoreconsole.exe` at `/mnt/c/opt/AutoCAD 2022/`

## API Endpoints

- `POST /convert` — multipart upload (`files`), optional `merge=true/false`, returns ZIP
- `GET /health` — returns `{"status": "ok"}`

```bash
curl -X POST http://localhost:5557/convert -F "files=@drawing.dwg" -F "merge=false" --output result.zip
```

## Language

Primary language is Chinese (README, docs, API docs). Code comments and docstrings are in Chinese. Variable/function names are in English.
