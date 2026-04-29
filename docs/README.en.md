# ACADxPDF

Batch AutoCAD DWG to PDF converter with automatic drawing border detection and per-border output.

## Features

- **Auto Border Detection** — Two strategies: block name matching (primary) + closed rectangle detection (fallback)
- **Per-border PDF Output** — Each detected border produces a separate PDF using Window mode for precise clipping
- **Adaptive Paper Size** — Automatically matches standard paper sizes (A0–A4) from border dimensions, supports extended sizes (e.g. A1+0.5, A1+1)
- **Batch Processing** — Directory batch conversion with stats (total time, PDF count, average time)
- **Flask API** — Upload DWG files via HTTP, receive a ZIP containing DWG + DXF + PDF
- **Optional Merging** — Adjacent borders can be merged into a single PDF

## Requirements

- Windows 10+
- AutoCAD 2020 (with `accoreconsole.exe`)
- Python 3.10+
- conda environment: `pdf`

## Install

```bash
conda activate pdf
pip install ezdxf flask
```

## Usage

### CLI — Single File

```python
from acad2pdf import convert_dwg

result = convert_dwg(
    r"C:\path\to\drawing.dwg",
    output_dir=r"C:\path\to\output",
    split_borders=True,
    auto_paper_size=True,
)
```

### CLI — Batch

```python
from acad2pdf import batch_convert

results = batch_convert(
    input_dir=r"C:\path\to\dwg_folder",
    output_dir=r"C:\path\to\output",
    split_borders=True,
    auto_paper_size=True,
)
```

### API Server

```bash
python -m acad2pdf.api
# Starts at http://0.0.0.0:5557
```

**Upload & Convert:**

```bash
curl -X POST http://localhost:5000/convert \
  -F "files=@drawing1.dwg" \
  -F "files=@drawing2.dwg" \
  -F "merge=false" \
  --output result.zip
```

The returned ZIP contains: original DWG, intermediate DXF, and per-border PDFs.

**Health Check:**

```bash
curl http://localhost:5000/health
```

## Output Naming

```
{number}-{DWG_filename}-{paper_size}.pdf
```

Example: `01-n-A1.pdf`, `02-n-A1+0.5.pdf`, `03-design_notes-A2.pdf`

## Project Structure

```
ACADxPDF/
├── acad2pdf/              # Core package
│   ├── __init__.py        # Public interface exports
│   ├── converter.py       # Core module (border detection, PDF generation, batch processing)
│   └── api.py             # Flask API service
├── tools/                 # Utility scripts
│   ├── analyze_dxf.py     # DXF analysis utility
│   └── test_api.py        # API test script
├── docs/                  # Documentation
│   ├── API说明文档.md
│   └── README.en.md
├── .env.example           # Config template
├── .gitignore
├── README.md
└── LICENSE
```

## Configuration

Copy `.env.example` to `.env` and modify as needed:

```ini
ACAD_PATH=C:\Autodesk\AutoCAD 2020\accoreconsole.exe
ACAD_UNIT=毫米
PRINTER=DWG To PDF.pc3
PLOT_STYLE=monochrome.ctb
TIMEOUT=180
BORDER_KEYWORDS=TK,TUKUANG,BORDER,FRAME,TITLE
API_HOST=0.0.0.0
API_PORT=5000
```

## Border Detection

1. **Block Name Matching** (primary) — Finds INSERT blocks with names containing keywords from `BORDER_KEYWORDS`, computes bounding boxes as border boundaries
2. **Rectangle Detection** (fallback) — Scans modelspace and block definitions for closed LWPOLYLINE rectangles, filters by standard paper size short-side match, removes inner contained rectangles

## License

MIT License. See [LICENSE](LICENSE).

Copyright (c) 2025 grigs28
