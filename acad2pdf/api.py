"""
ACADxPDF API - Flask + WebUI on same port.

POST /convert  - upload DWG, multi-threaded conversion, return JSON results.
GET  /stream   - SSE real-time progress.
GET  /config   - get runtime config.
POST /config   - update runtime config.
GET  /logs     - recent log lines.
GET  /download/<task_id> - download result ZIP.
GET  /callback - SSO ticket callback.
GET  /auth/check - check login status.
GET  /logout   - logout.
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

import requests as http_req
from flask import Flask, request, jsonify, Response, send_from_directory, send_file, session, redirect
from werkzeug.utils import secure_filename

from .converter import (
    convert_dwg_lsp,
    ConversionResult,
    DEFAULT_TIMEOUT,
    DEFAULT_PRINTER,
    DEFAULT_PLOT_STYLE,
    BORDER_KEYWORDS,
    WORK_DIR,
)

app = Flask(__name__, static_folder="static", static_url_path="/static")

# 自动生成 session secret
_session_secret_file = Path(__file__).parent.parent / ".session_secret"
if _session_secret_file.exists():
    app.secret_key = _session_secret_file.read_text().strip()
else:
    import secrets as _secrets
    app.secret_key = _secrets.token_hex(32)
    _session_secret_file.write_text(app.secret_key)

API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "5557"))
LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", 20 * 1024 * 1024))
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", 5))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 4))
SSO_URL = os.environ.get("SSO_URL", "http://192.168.0.8:80")

# API Key：首次启动自动生成，写入 .env
API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    import secrets as _secrets
    API_KEY = f"axp-{_secrets.token_hex(16)}"
    _env_path = Path(__file__).parent.parent / ".env"
    _env_lines = _env_path.read_text(encoding="utf-8").splitlines() if _env_path.exists() else []
    _env_lines.append(f"API_KEY={API_KEY}")
    _env_path.write_text("\n".join(_env_lines) + "\n", encoding="utf-8")
    os.environ["API_KEY"] = API_KEY

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
    "t3_mode": True,
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

# --- SSO 登录 ---

@app.route("/callback")
def sso_callback():
    """SSO ticket 回调：验证 ticket 后跳转回首页。"""
    ticket = request.args.get("ticket")
    if not ticket:
        return redirect(f"{SSO_URL}/login?from={request.host_url}callback")
    try:
        resp = http_req.get(f"{SSO_URL}/api/ticket/verify", params={"ticket": ticket}, timeout=10)
        if resp.status_code == 200 and resp.json().get("ok"):
            session["user"] = resp.json()
            return redirect("/")
    except Exception:
        pass
    return redirect(f"{SSO_URL}/login?from={request.host_url}callback")


@app.route("/auth/check")
def auth_check():
    """检查当前登录状态（Web UI 用，基于 session cookie）。"""
    user = session.get("user")
    if user:
        return jsonify({"ok": True, **user})
    return jsonify({"ok": False})


@app.route("/logout")
def logout():
    """退出登录。"""
    session.clear()
    return jsonify({"ok": True})


# --- API Key 认证（API 调用用） ---

def _check_api_key():
    """API 认证：session 登录 或 apikey 均可通过。"""
    if session.get("user"):
        return None
    key = request.headers.get("X-API-Key") or request.args.get("apikey")
    if API_KEY and key == API_KEY:
        return None
    return jsonify({"error": "unauthorized"}), 401
    return None


@app.route("/")
def index():
    if not session.get("user"):
        return redirect(f"{SSO_URL}/login?from={request.host_url}callback")
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
    err = _check_api_key()
    if err:
        return err
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files uploaded"}), 400

    dwg_files = [f for f in files if f.filename.lower().endswith(".dwg")]
    if not dwg_files:
        return jsonify({"error": "no DWG files"}), 400

    merge = request.form.get("merge", "false").lower() == "true"
    max_workers = int(request.form.get("workers", runtime_config["max_workers"]))

    # 可选参数：空值则使用 runtime_config 默认值
    plot_style = request.form.get("plot_style", "").strip() or runtime_config["plot_style"]
    border_kw = request.form.get("border_keywords", "").strip() or runtime_config["border_keywords"]

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
        # 每个 DWG 单独的输出子目录
        stem = Path(dwg_path).stem
        per_output_dir = os.path.join(output_dir, stem)
        os.makedirs(per_output_dir, exist_ok=True)
        # 复制原始 DWG 到输出子目录
        shutil.copy2(dwg_path, os.path.join(per_output_dir, os.path.basename(dwg_path)))
        cb = _progress_cb(task_id, filename)
        _sse_broadcast("file_start", {"task_id": task_id, "file": filename})
        try:
            r = convert_dwg_lsp(
                dwg_path, per_output_dir,
                printer=runtime_config["printer"],
                plot_style=plot_style,
                border_keywords=border_kw,
                plot_scale="Fit",
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
                "dwg_path": dwg_path,
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
                                "pdf_count": 0, "elapsed": 0, "borders": [], "dwg_path": ""})

        # Build per-DWG ZIPs, then a master ZIP
        master_zip_path = os.path.join(upload_dir, "result.zip")
        with zipfile.ZipFile(master_zip_path, "w", zipfile.ZIP_DEFLATED) as master_zf:
            for r in results:
                if not r["success"]:
                    continue
                dwg_path = r.get("dwg_path", "")
                if not dwg_path or not os.path.exists(dwg_path):
                    continue
                stem = Path(dwg_path).stem
                per_output_dir = os.path.join(output_dir, stem)
                # 所有文件放入 <stem>/ 子目录
                files = []
                # 1. DWG
                files.append((dwg_path, f"{stem}/{os.path.basename(dwg_path)}"))
                # 2. DXF + PDFs
                if os.path.isdir(per_output_dir):
                    for name in sorted(os.listdir(per_output_dir)):
                        if name.lower().endswith((".pdf", ".dxf")):
                            files.append((os.path.join(per_output_dir, name), f"{stem}/{name}"))
                for fpath, arcname in files:
                    master_zf.write(fpath, arcname)

        total_time = time.time() - task["start_time"]
        ok_count = sum(1 for r in results if r["success"])
        total_pdfs = sum(r["pdf_count"] for r in results)

        with _tasks_lock:
            task["status"] = "done"
            task["results"] = results
            task["zip_path"] = master_zip_path
            task["end_time"] = time.time()
            task["total_time"] = round(total_time, 1)
            task["ok_count"] = ok_count
            task["total_pdfs"] = total_pdfs
            task["zip_size_kb"] = round(os.path.getsize(master_zip_path) / 1024, 1)

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


@app.route("/plot-styles", methods=["GET"])
def list_plot_styles():
    """列出 plot_styles/ 目录下所有 .ctb 文件。"""
    from acad2pdf.converter import PLOT_STYLES_DIR
    os.makedirs(PLOT_STYLES_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(PLOT_STYLES_DIR) if f.lower().endswith(".ctb"))
    return jsonify({"files": files, "default": "monochrome.ctb"})


@app.route("/plot-styles/upload", methods=["POST"])
def upload_plot_style():
    """上传 CTB 文件到 plot_styles/ 目录。"""
    from acad2pdf.converter import PLOT_STYLES_DIR
    os.makedirs(PLOT_STYLES_DIR, exist_ok=True)
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "未上传文件"}), 400
    name = secure_filename(f.filename) or "uploaded.ctb"
    if not name.lower().endswith(".ctb"):
        name += ".ctb"
    f.save(os.path.join(PLOT_STYLES_DIR, name))
    return jsonify({"status": "ok", "name": name})


@app.route("/plot-styles/<name>", methods=["DELETE"])
def delete_plot_style(name):
    """删除指定 CTB 文件（至少保留一个）。"""
    from acad2pdf.converter import PLOT_STYLES_DIR
    safe = secure_filename(name)
    path = os.path.join(PLOT_STYLES_DIR, safe)
    if not os.path.isfile(path):
        return jsonify({"error": "文件不存在"}), 404
    remaining = [f for f in os.listdir(PLOT_STYLES_DIR) if f.lower().endswith(".ctb")]
    if len(remaining) <= 1:
        return jsonify({"error": "至少保留一个 CTB 文件"}), 400
    os.remove(path)
    return jsonify({"status": "ok"})


@app.route("/config", methods=["GET"])
def get_config():
    return jsonify(runtime_config)


@app.route("/config", methods=["POST"])
def update_config():
    if not session.get("user"):
        return jsonify({"error": "请先登录"}), 401
    data = request.get_json(force=True)
    allowed = {"printer", "plot_style", "timeout", "border_keywords",
               "merge_borders", "auto_paper_size", "split_borders", "max_workers",
               "t3_mode"}
    updated = {}
    for k, v in data.items():
        if k in allowed:
            if k == "timeout":
                v = int(v)
            elif k == "max_workers":
                v = max(1, int(v))
            elif k in ("merge_borders", "auto_paper_size", "split_borders", "t3_mode"):
                v = bool(v)
            runtime_config[k] = v
            updated[k] = v
    if updated:
        log.info("Config updated: %s", updated)
    return jsonify({"status": "ok", "updated": updated})


def _workers_json_path():
    return Path(__file__).parent.parent / "workers.json"


def _read_workers_config():
    p = _workers_json_path()
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"workers": [], "acad_exe": "", "timeout": 300}


def _write_workers_config(cfg):
    _workers_json_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@app.route("/admin/config", methods=["GET"])
def admin_get_config():
    user = session.get("user")
    if not user or user.get("is_admin") != 1:
        return jsonify({"error": "需要管理员权限"}), 403
    from . import converter
    wc = _read_workers_config()
    return jsonify({
        "api_key": API_KEY,
        "acad_path": converter.ACCORE,
        "acad_exe": converter.ACAD_EXE,
        "tarch_arx": converter.TARCH_ARX,
        "acad_template": converter.ACAD_TEMPLATE,
        "work_dir": converter.WORK_DIR,
        "workers": wc.get("workers", []),
        "pdf_timeout": wc.get("timeout", 300),
        # 打印配置
        "printer": runtime_config["printer"],
        "plot_style": runtime_config["plot_style"],
        "timeout": runtime_config["timeout"],
        "border_keywords": runtime_config["border_keywords"],
        "max_workers": runtime_config["max_workers"],
    })


@app.route("/admin/config", methods=["POST"])
def admin_set_config():
    user = session.get("user")
    if not user or user.get("is_admin") != 1:
        return jsonify({"error": "需要管理员权限"}), 403
    data = request.get_json(force=True)
    from . import converter
    env_path = Path(__file__).parent.parent / ".env"
    env_map = {
        "api_key": "API_KEY",
        "acad_path": "ACAD_PATH",
        "acad_exe": "ACAD_EXE",
        "tarch_arx": "TARCH_ARX",
        "acad_template": "ACAD_TEMPLATE",
        "work_dir": "WORK_DIR",
    }
    # 读取现有 .env
    env_lines = []
    if env_path.exists():
        env_lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = {}
    for key, env_name in env_map.items():
        if key in data:
            val = str(data[key]).strip()
            # 更新运行时值
            if key == "api_key":
                global API_KEY
                API_KEY = val
            elif key == "acad_path":
                converter.ACCORE = val
            elif key == "acad_exe":
                converter.ACAD_EXE = val
            elif key == "tarch_arx":
                converter.TARCH_ARX = val
            elif key == "acad_template":
                converter.ACAD_TEMPLATE = val
            elif key == "work_dir":
                converter.WORK_DIR = val
            # 更新 .env 文件
            found = False
            for i, line in enumerate(env_lines):
                if line.startswith(f"{env_name}="):
                    env_lines[i] = f"{env_name}={val}"
                    found = True
                    break
            if not found:
                env_lines.append(f"{env_name}={val}")
            updated[key] = val
    if updated:
        env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        log.info("Admin config updated: %s", list(updated.keys()))

    # Workers 配置写入 workers.json
    if "workers" in data or "pdf_timeout" in data:
        wc = _read_workers_config()
        if "workers" in data:
            wc["workers"] = data["workers"]
        if "pdf_timeout" in data:
            wc["timeout"] = int(data["pdf_timeout"])
        if "acad_exe" in data:
            wc["acad_exe"] = str(data["acad_exe"]).strip()
        _write_workers_config(wc)
        log.info("Workers config updated")

    # 打印配置持久化到 .env
    print_keys = {"printer": "PRINTER", "plot_style": "PLOT_STYLE", "timeout": "TIMEOUT",
                  "border_keywords": "BORDER_KEYWORDS", "max_workers": "MAX_WORKERS"}
    print_updated = {}
    for key, env_name in print_keys.items():
        if key in data:
            val = data[key]
            if key in ("timeout", "max_workers"):
                val = max(1, int(val))
            elif key == "max_workers":
                val = max(1, int(val))
            runtime_config[key] = val
            # 写入 .env
            val_str = str(val)
            found = False
            for i, line in enumerate(env_lines):
                if line.startswith(f"{env_name}="):
                    env_lines[i] = f"{env_name}={val_str}"
                    found = True
                    break
            if not found:
                env_lines.append(f"{env_name}={val_str}")
            print_updated[key] = val
    if print_updated:
        env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
        log.info("Print config updated: %s", print_updated)

    return jsonify({"status": "ok", "updated": {**updated, **print_updated}})


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
    log.info("API Key: %s", API_KEY)
    app.run(host=API_HOST, port=API_PORT, threaded=True)
