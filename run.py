"""
ACADxPDF 启动入口 — 统一调度架构（DWG→PDF + PDF→DWG）。

注册所有 Blueprint，启动本地 Worker 后台线程。
"""

import os
import shutil
import threading
from flask import send_from_directory
from acad2pdf.api import app, API_HOST, API_PORT, MAX_WORKERS, log
from acad2pdf.pdf2dwg_api import pdf2dwg_bp
from acad2pdf.dispatch_routes import dispatch_bp
from acad2pdf.worker import Worker, start_worker_threads

# 注册 Blueprint
app.register_blueprint(pdf2dwg_bp)
app.register_blueprint(dispatch_bp)


# 启动时清空临时文件
def _cleanup_temp_dirs():
    """清空 _work/ 和 output/ 目录中的残留文件。"""
    project_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ("_work", "output"):
        d = os.path.join(project_dir, name)
        if os.path.isdir(d):
            try:
                count = sum(1 for _ in os.scandir(d))
                if count > 0:
                    shutil.rmtree(d, ignore_errors=True)
                    os.makedirs(d, exist_ok=True)
                    log.info("Cleaned %s/ (%d items)", name, count)
            except Exception as ex:
                log.warning("Failed to clean %s/: %s", name, ex)

_cleanup_temp_dirs()


# 用 before_request 覆盖 / 路由，返回新版前端（保留 SSO 认证）
@app.before_request
def _override_index():
    from flask import request, session
    if request.path == "/":
        if not session.get("user"):
            from acad2pdf.api import SSO_URL
            return __import__("flask").redirect(f"{SSO_URL}/login?from={request.host_url}callback")
        static_dir = os.path.join(os.path.dirname(__file__), "acad2pdf", "static")
        return send_from_directory(static_dir, "index.html")


def start_local_worker():
    """启动本地 Worker（后台线程）。直接用 store 注册，不走 HTTP。"""
    import time
    from acad2pdf.api import API_KEY, runtime_config
    from acad2pdf.task_store import store

    capacity = runtime_config.get("max_workers", MAX_WORKERS)
    store.register_worker("local", capacity)

    worker = Worker(
        worker_id="local",
        capacity=capacity,
        master_url=f"http://127.0.0.1:{API_PORT}",
        api_key=API_KEY,
        acad_exe=os.environ.get("ACAD_EXE", r"C:\opt\AutoCAD 2026\acad.exe"),
        timeout=runtime_config.get("timeout", 300),
    )
    threads = start_worker_threads(worker, worker.capacity)

    # 心跳线程：直接更新 store
    def heartbeat_loop():
        while True:
            time.sleep(30)
            store.heartbeat("local", worker._active, worker._done)

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    return worker, threads


if __name__ == "__main__":
    log.info("ACADxPDF starting on %s:%s (workers=%d) [unified dispatch]",
             API_HOST, API_PORT, MAX_WORKERS)
    worker, _ = start_local_worker()
    log.info("Local worker started (capacity=%d)", worker.capacity)
    app.run(host=API_HOST, port=API_PORT, threaded=True)
