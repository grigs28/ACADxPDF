# acad2pdf/dispatch_routes.py
"""拉取式调度端点 — Worker 通过这些 API 拉取任务、回传结果。"""

import json
import logging
import os
import zipfile

from flask import Blueprint, request, jsonify, send_file, session
from werkzeug.utils import secure_filename

from .task_store import store

log = logging.getLogger("acad2pdf")

dispatch_bp = Blueprint("dispatch", __name__)


def _check_api_key():
    """Worker 认证：API Key。"""
    from .api import API_KEY
    key = request.headers.get("X-API-Key") or request.args.get("apikey")
    if API_KEY and key == API_KEY:
        return None
    return jsonify({"error": "unauthorized"}), 401


@dispatch_bp.route("/dispatch/register", methods=["POST"])
def register():
    err = _check_api_key()
    if err:
        return err
    data = request.get_json(force=True)
    worker_id = data.get("worker_id")
    capacity = data.get("capacity", 4)
    if not worker_id:
        return jsonify({"error": "worker_id required"}), 400
    w = store.register_worker(worker_id, capacity)
    return jsonify({"status": "ok", "worker_id": w.worker_id})


@dispatch_bp.route("/dispatch/heartbeat", methods=["POST"])
def heartbeat():
    err = _check_api_key()
    if err:
        return err
    data = request.get_json(force=True)
    worker_id = data.get("worker_id")
    if not worker_id:
        return jsonify({"error": "worker_id required"}), 400
    store.heartbeat(worker_id, data.get("active_slots"), data.get("done_count"))
    return jsonify({"status": "ok"})


@dispatch_bp.route("/dispatch/unregister", methods=["POST"])
def unregister():
    err = _check_api_key()
    if err:
        return err
    data = request.get_json(force=True)
    worker_id = data.get("worker_id")
    if not worker_id:
        return jsonify({"error": "worker_id required"}), 400
    store.unregister_worker(worker_id)
    return jsonify({"status": "ok"})


@dispatch_bp.route("/dispatch/pull", methods=["POST"])
def pull():
    err = _check_api_key()
    if err:
        return err
    data = request.get_json(force=True)
    worker_id = data.get("worker_id")
    capacity = min(data.get("capacity", 1), 16)
    if not worker_id:
        return jsonify({"error": "worker_id required"}), 400
    files = store.pull(worker_id, capacity)
    if not files:
        return jsonify({"files": []})
    for f in files:
        f["download_url"] = f"/dispatch/file/{f['file_id']}"
    log.info("Worker %s pulled %d files", worker_id, len(files))
    return jsonify({"files": files})


@dispatch_bp.route("/dispatch/result", methods=["POST"])
def report_result():
    err = _check_api_key()
    if err:
        return err
    file_id = request.form.get("file_id")
    success = request.form.get("success", "false").lower() == "true"
    error = request.form.get("error", "")
    elapsed = float(request.form.get("elapsed", "0"))
    metadata_str = request.form.get("metadata", "{}")
    metadata = json.loads(metadata_str) if metadata_str else {}

    if not file_id:
        return jsonify({"error": "file_id required"}), 400

    found = store.find_file(file_id)
    if not found:
        return jsonify({"error": "file not found"}), 404
    task, f = found

    output_files = []
    output_dir = os.path.join(task.results_dir, f.id)
    os.makedirs(output_dir, exist_ok=True)

    for key in request.files:
        uploaded = request.files[key]
        if uploaded.filename:
            safe = secure_filename(uploaded.filename) or f"{key}_{file_id}"
            dest = os.path.join(output_dir, safe)
            uploaded.save(dest)
            output_files.append(dest)

    store.report_result(file_id, success, output_files, error, elapsed, metadata)

    log.info("Result reported: %s success=%s elapsed=%.1f outputs=%d",
             file_id, success, elapsed, len(output_files))

    if task.all_done():
        _finalize_task(task)

    return jsonify({"status": "ok"})


@dispatch_bp.route("/dispatch/file/<file_id>")
def download_source(file_id):
    """Worker 下载源文件。"""
    err = _check_api_key()
    if err:
        return err
    found = store.find_file(file_id)
    if not found:
        return jsonify({"error": "file not found"}), 404
    task, f = found
    if not os.path.exists(f.source_path):
        return jsonify({"error": "source file not found on disk"}), 404
    return send_file(f.source_path, as_attachment=True, download_name=f.name)


def _finalize_task(task):
    """任务全部完成：打 ZIP，广播 SSE。"""
    from .api import _sse_broadcast

    zip_path = os.path.join(task.results_dir, "result.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in task.files:
            fdir = os.path.join(task.results_dir, f.id)
            if not os.path.isdir(fdir):
                continue
            stem = os.path.splitext(f.name)[0]
            for name in sorted(os.listdir(fdir)):
                if name.lower().endswith((".pdf", ".dwg", ".dxf")):
                    zf.write(os.path.join(fdir, name), f"{stem}/{name}")

    task.zip_path = zip_path
    if os.path.exists(zip_path):
        task.zip_size_kb = round(os.path.getsize(zip_path) / 1024, 1)

    total_pdfs = 0
    if task.type == "dwg2pdf":
        for f in task.files:
            fdir = os.path.join(task.results_dir, f.id)
            if f.status == "done" and os.path.isdir(fdir):
                total_pdfs += len([n for n in os.listdir(fdir) if n.lower().endswith(".pdf")])

    event = "task_done" if task.type == "dwg2pdf" else "pdf_task_done"
    _sse_broadcast(event, {
        "task_id": task.id,
        "total_time": task.total_time,
        "ok_count": task.ok_count,
        "total": task.total,
        "total_pdfs": total_pdfs,
        "zip_size_kb": task.zip_size_kb,
    })
    log.info("Task %s finalized: %d/%d OK, %.1fs", task.id, task.ok_count, task.total, task.total_time)


@dispatch_bp.route("/admin/workers")
def admin_workers():
    """查看 Worker 状态。"""
    user = session.get("user")
    if not user:
        key = request.headers.get("X-API-Key") or request.args.get("apikey")
        from .api import API_KEY
        if not (API_KEY and key == API_KEY):
            return jsonify({"error": "unauthorized"}), 401
    store.recover_stale()
    return jsonify({"workers": store.get_workers()})
