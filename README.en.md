# ACADxPDF

Batch AutoCAD DWG to PDF converter with automatic drawing border detection and per-border output.

## Features

- **Auto Border Detection** — Two strategies: closed rectangle detection (primary) + block name matching (fallback)
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
python api.py
# Starts at http://0.0.0.0:5000
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
├── acad2pdf.py        # Core module (border detection, PDF generation, batch processing)
├── api.py             # Flask API service
├── analyze_dxf.py     # DXF analysis utility
├── test_api.py        # API test script
└── .gitignore
```

## Border Detection

1. **Rectangle Detection** (primary) — Scans modelspace and block definitions for closed LWPOLYLINE rectangles, filters by standard paper size short-side match, removes inner contained rectangles
2. **Block Name Matching** (fallback) — Finds INSERT blocks with names containing TK/BORDER/FRAME keywords, computes bounding boxes as border boundaries

## License

MIT License. See [LICENSE](LICENSE).

Copyright (c) 2025 grigs28
