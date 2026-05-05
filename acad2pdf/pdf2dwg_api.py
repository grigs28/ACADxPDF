"""
PDF→DWG Flask Blueprint — 多机调度路由。

所有 PDF→DWG 相关 API 路由，以 Blueprint 方式注册到主 app。
不修改原始 api.py。
"""

import json
import logging
import os
import time
import uuid

from flask import Blueprint, request, jsonify, send_from_directory, send_file
from werkzeug.utils import secure_filename

from .task_store import store

log = logging.getLogger("acad2pdf")

pdf2dwg_bp = Blueprint("pdf2dwg", __name__,
                        static_folder="static", static_url_path="/static")


@pdf2dwg_bp.route("/index2.html")
def index2():
    """PDF→DWG 增强版前端"""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    index2_path = os.path.join(static_dir, "index2.html")
    if os.path.exists(index2_path):
        return send_from_directory(static_dir, "index2.html")
    return jsonify({"status": "ok", "message": "index2.html not found"}), 404

ACAD_EXE = os.environ.get("ACAD_EXE", r"C:\opt\AutoCAD 2026\acad.exe")

# 从 api.py 获取配置（延迟导入避免循环依赖）
def _get_work_dir():
    from .converter import WORK_DIR
    return WORK_DIR

def _sse_broadcast(event, data):
    from .api import _sse_broadcast as _broadcast
    _broadcast(event, data)



def _load_workers_config():
    """加载 workers.json 配置"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workers.json")
    if not os.path.exists(config_path):
        return {
            "workers": [{"name": "local", "url": "http://localhost:5557", "max_slots": 4}],
            "acad_exe": ACAD_EXE,
        }
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ===================== 路由 =====================


@pdf2dwg_bp.route("/convert-pdf", methods=["POST"])
def convert_pdf_batch():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files uploaded"}), 400

    pdf_files = [f for f in files if f.filename.lower().endswith(".pdf")]
    if not pdf_files:
        return jsonify({"error": "no PDF files"}), 400

    task_id = uuid.uuid4().hex[:12]
    work_dir_root = _get_work_dir()
    results_dir = os.path.join(
        work_dir_root or os.path.join(os.path.dirname(os.path.dirname(__file__)), "output"),
        f"pdf2dwg_{task_id}")
    upload_dir = os.path.join(results_dir, "upload")
    os.makedirs(upload_dir, exist_ok=True)

    config = _load_workers_config()
    task = store.create_task("pdf2dwg", {
        "acad_exe": config.get("acad_exe", ACAD_EXE),
        "timeout": int(os.environ.get("PDF_TIMEOUT", "300")),
    }, results_dir=results_dir)

    for f in pdf_files:
        safe_name = secure_filename(f.filename) or f"{uuid.uuid4().hex[:8]}.pdf"
        p = os.path.join(upload_dir, safe_name)
        f.save(p)
        task.add_file(f.filename, p)

    store.start_task(task)

    _sse_broadcast("pdf_task_start", {"task_id": task.id, "total": task.total})
    log.info("PDF task %s: %d files queued", task.id, task.total)

    return jsonify({"task_id": task.id, "status": "running", "total": task.total})


@pdf2dwg_bp.route("/convert-pdf/add/<task_id>", methods=["POST"])
def convert_pdf_add(task_id):
    task = store.get_task(task_id)
    if not task or task.status != "running":
        return jsonify({"error": "task not found or not running"}), 404

    files = request.files.getlist("files")
    pdf_files = [f for f in files if f.filename.lower().endswith(".pdf")]
    if not pdf_files:
        return jsonify({"error": "no PDF files"}), 400

    upload_dir = os.path.join(task.results_dir, "upload")
    added = []
    for f in pdf_files:
        safe_name = secure_filename(f.filename) or f"{uuid.uuid4().hex[:8]}.pdf"
        p = os.path.join(upload_dir, safe_name)
        f.save(p)
        task.add_file(f.filename, p)
        added.append(f.filename)

    _sse_broadcast("pdf_task_add", {"task_id": task_id, "added": len(added), "total": task.total})
    return jsonify({"task_id": task_id, "added": len(added), "total": task.total})



@pdf2dwg_bp.route("/download-pdf-zip/<task_id>")
def download_pdf_zip(task_id):
    task = store.get_task(task_id)
    if not task or not task.zip_path or not os.path.exists(task.zip_path):
        return jsonify({"error": "no result"}), 404
    return send_file(task.zip_path, as_attachment=True, download_name=f"pdf2dwg_{task_id}.zip")


@pdf2dwg_bp.route("/pdf-task/<task_id>")
def get_pdf_task(task_id):
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task.to_dict())


@pdf2dwg_bp.route("/pdf-tasks")
def list_pdf_tasks():
    return jsonify(store.list_tasks())
