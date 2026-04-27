"""
ACADxPDF API - Flask service for DWG to PDF conversion.

POST /convert - upload DWG files, get back ZIP with DWG+DXF+PDF.
  Form params: files (multiple), merge (optional, "true"/"false", default "false")
"""

import os
import shutil
import tempfile
import zipfile
from flask import Flask, request, send_file, jsonify

from acad2pdf import convert_dwg, DEFAULT_TIMEOUT

app = Flask(__name__)

API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "5000"))


@app.route("/convert", methods=["POST"])
def convert():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files uploaded"}), 400

    merge = request.form.get("merge", "false").lower() == "true"

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

            r = convert_dwg(
                dwg_path, output_dir,
                split_borders=True, auto_paper_size=True,
                merge_borders=merge,
            )
            results.append(r.to_dict())

        if not results:
            return jsonify({"error": "no valid DWG files"}), 400

        # Package all output into a ZIP
        zip_path = os.path.join(work_dir, "result.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in results:
                if os.path.exists(r["dwg"]):
                    zf.write(r["dwg"], os.path.basename(r["dwg"]))

            for name in os.listdir(output_dir):
                full = os.path.join(output_dir, name)
                if os.path.isfile(full) and not name.startswith("_temp_"):
                    zf.write(full, name)

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
    app.run(host=API_HOST, port=API_PORT)
