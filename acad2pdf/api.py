"""
ACADxPDF API - Flask + WebUI on same port.

POST /convert  - upload DWG, multi-threaded conversion, return JSON results.
GET  /stream   - SSE real-time progress.
GET  /config   - get runtime config.
POST /config   - update runtime config.
GET  /logs     - recent log lines.
GET  /download/<task_id> - download result ZIP.
GET  /         - WebUI.
"""

import json
import logging
import os
import queue
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, request, jsonify, Response, send_from_directory, send_file
from werkzeug.utils import secure_filename

from .converter import (
    convert_dwg,
    ConversionResult,
    DEFAULT_TIMEOUT,
    DEFAULT_PRINTER,
    DEFAULT_PLOT_STYLE,
    BORDER_KEYWORDS,
    WORK_DIR,
)

app = Flask(__name__, static_folder="static", static_url_path="/static")

API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "5557"))
LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", 20 * 1024 * 1024))
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", 5))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 4))

# --- Runtime config ---
runtime_config = {
    "printer": DEFAULT_PRINTER,
    "plot_style": DEFAULT_PLOT_STYLE,
    "timeout": DEFAULT_TIMEOUT,
    "border_keywords": ",".join(BORDER_KEYWORDS),
    "merge_borders": False,
    "auto_paper_size": True,
    "split_borders": True,
    "max_workers": MAX_WORKERS,
}

# --- Task store: task_id -> {status, files, results, zip_path, ...} ---
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()

# --- SSE ---
_sse_queues: dict[str, queue.Queue] = {}
_sse_lock = threading.Lock()

# --- Thread pool ---
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="acad2pdf")

# --- Logging ---
log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(log_dir, exist_ok=True)

log = logging.getLogger("acad2pdf")
log.setLevel(logging.INFO)

_fh = RotatingFileHandler(
    os.path.join(log_dir, "api.log"),
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
    encoding="utf-8",
)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(_fh)

_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(_sh)


def _sse_broadcast(event: str, data: dict):
    msg = json.dumps(data, ensure_ascii=False)
    dead = []
    with _sse_lock:
        for sid, q in _sse_queues.items():
            try:
                q.put_nowait(f"event: {event}\ndata: {msg}\n\n")
            except Exception:
                dead.append(sid)
        for sid in dead:
            _sse_queues.pop(sid, None)


def _progress_cb(task_id: str, filename: str):
    def cb(event: str, data: dict):
        data["task_id"] = task_id
        data["file"] = data.get("file", filename)
        _sse_broadcast(event, data)
        log.info("[T:%s] [%s] %s", task_id[:6], event, json.dumps(data, ensure_ascii=False))
    return cb


# ===================== Routes =====================

@app.route("/")
def index():
    static_dir = Path(__file__).parent / "static"
    if (static_dir / "index.html").exists():
        return send_from_directory(str(static_dir), "index.html")
    return jsonify({"status": "ok", "message": "ACADxPDF API"})


@app.route("/stream")
def stream():
    sid = uuid.uuid4().hex[:8]
    q = queue.Queue(maxsize=500)
    with _sse_lock:
        _sse_queues[sid] = q

    def generate():
        try:
            yield f"event: connected\ndata: {json.dumps({'sid': sid})}\n\n"
            while True:
                try:
                    yield q.get(timeout=30)
                except queue.Empty:
                    yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                _sse_queues.pop(sid, None)

    resp = Response(generate(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/convert", methods=["POST"])
def convert():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files uploaded"}), 400

    dwg_files = [f for f in files if f.filename.lower().endswith(".dwg")]
    if not dwg_files:
        return jsonify({"error": "no DWG files"}), 400

    merge = request.form.get("merge", "false").lower() == "true"
    max_workers = int(request.form.get("workers", runtime_config["max_workers"]))

    task_id = uuid.uuid4().hex[:12]

    # 上传文件保存到 Windows 文件系统（如果 WORK_DIR 配置了）
    # 这样 accoreconsole 才能访问
    project_dir = os.path.dirname(os.path.dirname(__file__))
    if WORK_DIR:
        upload_dir = os.path.join(WORK_DIR, task_id)
    else:
        upload_dir = os.path.join(project_dir, "output", task_id)
    output_dir = os.path.join(upload_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    # Save uploaded files (sanitize filenames)
    saved = []
    for f in dwg_files:
        safe_name = secure_filename(f.filename) or f"{uuid.uuid4().hex[:8]}.dwg"
        p = os.path.join(upload_dir, safe_name)
        f.save(p)
        saved.append((f.filename, p))

    task = {
        "id": task_id,
        "status": "running",
        "total": len(saved),
        "files": [s[0] for s in saved],
        "results": [],
        "zip_path": "",
        "start_time": time.time(),
        "end_time": 0,
        "workers": max_workers,
    }
    with _tasks_lock:
        _tasks[task_id] = task

    _sse_broadcast("task_start", {"task_id": task_id, "total": len(saved), "workers": max_workers})
    log.info("Task %s: %d files, %d workers, merge=%s", task_id, len(saved), max_workers, merge)

    # Submit to thread pool
    def _do_convert(filename, dwg_path):
        t0 = time.time()
        cb = _progress_cb(task_id, filename)
        _sse_broadcast("file_start", {"task_id": task_id, "file": filename})
        try:
            r = convert_dwg(
                dwg_path, output_dir,
                split_borders=runtime_config["split_borders"],
                auto_paper_size=runtime_config["auto_paper_size"],
                merge_borders=merge or runtime_config["merge_borders"],
                printer=runtime_config["printer"],
                plot_style=runtime_config["plot_style"],
                timeout=runtime_config["timeout"],
                progress_callback=cb,
            )
        except Exception as ex:
            r = ConversionResult(dwg_path=dwg_path, error=str(ex), elapsed=time.time() - t0)
            log.error("Task %s: convert exception for %s: %s", task_id[:6], filename, ex)

        elapsed = time.time() - t0
        pdf_count = len(r.borders) if r.borders else (1 if r.success else 0)
        _sse_broadcast("file_done", {
            "task_id": task_id, "file": filename,
            "success": r.success, "pdf_count": pdf_count,
            "elapsed": round(elapsed, 1), "error": r.error,
        })
        log.info("Task %s: %s %s (%.1fs, %d PDFs)", task_id[:6],
                 "OK" if r.success else "FAIL", filename, elapsed, pdf_count)
        return {"file": filename, "success": r.success, "error": r.error,
                "pdf_count": pdf_count, "elapsed": round(elapsed, 1),
                "borders": [{"name": b.name, "size_label": b.size_label,
                             "width_mm": round(b.paper_width_mm), "height_mm": round(b.paper_height_mm)}
                            for b in (r.borders or [])]}

    futures = []
    for filename, dwg_path in saved:
        ft = _executor.submit(_do_convert, filename, dwg_path)
        futures.append(ft)

    # Collect results in background, then build ZIP
    def _collect():
        results = []
        for ft in futures:
            try:
                results.append(ft.result())
            except Exception as ex:
                results.append({"file": "?", "success": False, "error": str(ex),
                                "pdf_count": 0, "elapsed": 0, "borders": []})

        # Build ZIP
        zip_path = os.path.join(upload_dir, "result.zip")
        written = set()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in os.listdir(output_dir):
                full = os.path.join(output_dir, name)
                if os.path.isfile(full) and not name.startswith(("_temp_", "_input_", "_plot_", "_dwg2dxf")):
                    if full not in written:
                        zf.write(full, name)
                        written.add(full)

        total_time = time.time() - task["start_time"]
        ok_count = sum(1 for r in results if r["success"])
        total_pdfs = sum(r["pdf_count"] for r in results)

        with _tasks_lock:
            task["status"] = "done"
            task["results"] = results
            task["zip_path"] = zip_path
            task["end_time"] = time.time()
            task["total_time"] = round(total_time, 1)
            task["ok_count"] = ok_count
            task["total_pdfs"] = total_pdfs
            task["zip_size_kb"] = round(os.path.getsize(zip_path) / 1024, 1)

        _sse_broadcast("task_done", {
            "task_id": task_id, "total_time": round(total_time, 1),
            "ok_count": ok_count, "total": len(results),
            "total_pdfs": total_pdfs, "workers": max_workers,
            "zip_size_kb": task["zip_size_kb"],
        })
        log.info("Task %s done: %d/%d OK, %d PDFs, %.1fs (%d workers)",
                 task_id, ok_count, len(results), total_pdfs, total_time, max_workers)

    threading.Thread(target=_collect, daemon=True).start()

    return jsonify({"task_id": task_id, "status": "running",
                     "total": len(saved), "workers": max_workers})


@app.route("/task/<task_id>")
def get_task(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    out = {k: v for k, v in task.items() if k != "zip_path"}
    return jsonify(out)


@app.route("/download/<task_id>")
def download(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task or not task.get("zip_path") or not os.path.exists(task["zip_path"]):
        return jsonify({"error": "no result"}), 404
    return send_file(task["zip_path"], as_attachment=True, download_name=f"{task_id}.zip")


@app.route("/tasks")
def list_tasks():
    with _tasks_lock:
        out = []
        for tid, t in _tasks.items():
            out.append({"id": tid, "status": t["status"], "total": t["total"],
                        "ok_count": t.get("ok_count", 0), "total_time": t.get("total_time", 0),
                        "workers": t.get("workers", 0)})
    return jsonify(out)


@app.route("/config", methods=["GET"])
def get_config():
    return jsonify(runtime_config)


@app.route("/config", methods=["POST"])
def update_config():
    data = request.get_json(force=True)
    allowed = {"printer", "plot_style", "timeout", "border_keywords",
               "merge_borders", "auto_paper_size", "split_borders", "max_workers"}
    updated = {}
    for k, v in data.items():
        if k in allowed:
            if k == "timeout":
                v = int(v)
            elif k == "max_workers":
                v = max(1, int(v))
            elif k in ("merge_borders", "auto_paper_size", "split_borders"):
                v = bool(v)
            runtime_config[k] = v
            updated[k] = v
    if updated:
        log.info("Config updated: %s", updated)
    return jsonify({"status": "ok", "updated": updated})


@app.route("/logs", methods=["GET"])
def get_logs():
    lines = int(request.args.get("lines", 200))
    log_file = os.path.join(log_dir, "api.log")
    if not os.path.exists(log_file):
        return jsonify({"logs": []})
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    return jsonify({"logs": all_lines[-lines:]})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "workers": runtime_config["max_workers"]})


# --- Task reaper: clean up tasks older than 1 hour ---
_TASK_TTL = 3600


def _reap_tasks():
    while True:
        time.sleep(300)
        now = time.time()
        with _tasks_lock:
            expired = [tid for tid, t in _tasks.items()
                       if t["status"] == "done" and now - t.get("end_time", now) > _TASK_TTL]
            for tid in expired:
                task = _tasks.pop(tid)
                work_dir = os.path.dirname(task.get("zip_path", ""))
                if work_dir:
                    shutil.rmtree(work_dir, ignore_errors=True)
                log.info("Reaped task %s", tid)


threading.Thread(target=_reap_tasks, daemon=True).start()


if __name__ == "__main__":
    log.info("ACADxPDF API starting on %s:%s (workers=%d)", API_HOST, API_PORT, MAX_WORKERS)
    app.run(host=API_HOST, port=API_PORT, threaded=True)
