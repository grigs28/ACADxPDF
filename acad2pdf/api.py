"""
ACADxPDF API - Flask service for DWG to PDF conversion.

POST /convert - upload DWG files, get back ZIP with DWG+DXF+PDF.
  Form params: files (multiple), merge (optional, "true"/"false", default "false")
"""

import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from flask import Flask, request, send_file, jsonify

from .converter import convert_dwg, DEFAULT_TIMEOUT

app = Flask(__name__)

API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "5000"))

# --- Logging ---
log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "api.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("acad2pdf")


@app.route("/convert", methods=["POST"])
def convert():
    files = request.files.getlist("files")
    if not files:
        log.warning("Convert request with no files")
        return jsonify({"error": "no files uploaded"}), 400

    merge = request.form.get("merge", "false").lower() == "true"
    filenames = [f.filename for f in files if f.filename.lower().endswith(".dwg")]
    log.info("Convert request: %d DWG files, merge=%s", len(filenames), merge)

    work_dir = tempfile.mkdtemp(prefix="acad2pdf_")
    output_dir = os.path.join(work_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    results = []
    try:
        for f in files:
            if not f.filename.lower().endswith(".dwg"):
                continue
            dwg_path = os.path.join(work_dir, f.filename)
            f.save(dwg_path)
            log.info("Processing: %s", f.filename)

            r = convert_dwg(
                dwg_path, output_dir,
                split_borders=True, auto_paper_size=True,
                merge_borders=merge,
            )
            # Rename DXF temp file to formal name before packaging
            dxf_src = r.to_dict().get("dxf", "")
            if dxf_src and os.path.exists(dxf_src):
                stem = Path(dwg_path).stem
                dxf_dst = os.path.join(output_dir, f"{stem}.dxf")
                os.rename(dxf_src, dxf_dst)
                r.dxf_path = dxf_dst
            results.append(r)
            log.info("Done: %s -> %d PDFs (%.1fs)%s",
                     f.filename,
                     len(r.borders) if r.borders else 1,
                     r.elapsed,
                     "" if r.success else f" ERROR: {r.error}")

        if not results:
            log.warning("No valid DWG files in request")
            return jsonify({"error": "no valid DWG files"}), 400

        # Package all output into a ZIP
        zip_path = os.path.join(work_dir, "result.zip")
        written = set()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in results:
                rd = r.to_dict()
                for key in ("dwg", "dxf"):
                    p = rd.get(key, "")
                    if p and os.path.exists(p) and p not in written:
                        zf.write(p, os.path.basename(p))
                        written.add(p)

            for name in os.listdir(output_dir):
                full = os.path.join(output_dir, name)
                if os.path.isfile(full) and not name.startswith("_temp_") and full not in written:
                    zf.write(full, name)
                    written.add(full)

        log.info("ZIP ready: %d files, %.1f KB", len(written),
                 os.path.getsize(zip_path) / 1024)
        return send_file(zip_path, as_attachment=True, download_name="result.zip")

    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    log.info("ACADxPDF API starting on %s:%s", API_HOST, API_PORT)
    app.run(host=API_HOST, port=API_PORT)
