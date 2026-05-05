# 统一调度架构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 DWG→PDF 和 PDF→DWG 统一到拉取式 Worker 调度架构，外部程序和前端 API 不变。

**Architecture:** 主 API 内部维护统一任务队列，Worker 通过 HTTP 拉取文件、执行转换、回传结果。同一 Flask 进程同时提供主 API 和 Worker 端点。本地 Worker 作为后台线程自动启动。

**Tech Stack:** Python 3.10+, Flask, subprocess (acad.exe), threading

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `acad2pdf/task_store.py` | **新建** — 统一任务模型 + 内存队列 + Worker 注册表 | 新建 |
| `acad2pdf/dispatch_routes.py` | **新建** — `/dispatch/*` 端点（pull/result/register/heartbeat/unregister/file） | 新建 |
| `acad2pdf/worker.py` | **新建** — Worker 后台线程：pull→convert→result 循环 | 新建 |
| `acad2pdf/dispatcher.py` | **重写** — 清空旧 SWRR 推送代码，保留文件但内容替换 | 重写 |
| `acad2pdf/api.py` | **改造** — `/convert` 改用 task_store，移除 `_tasks`/`_executor` | 改造 |
| `acad2pdf/pdf2dwg_api.py` | **改造** — `/convert-pdf` 改用 task_store，移除 `_pdf_tasks` | 改造 |
| `acad2pdf/converter.py` | **不修改** — Worker 调用现有 `convert_dwg_lsp()` | 不动 |
| `acad2pdf/pdf2dwg_worker.py` | **不修改** — Worker 调用现有 `convert_one_pdf()` | 不动 |
| `acad2pdf/static/index.html` | **小改** — admin/workers 显示区域 | 小改 |
| `run.py` | **改造** — 注册 dispatch_routes Blueprint，启动本地 Worker | 改造 |

---

### Task 1: 统一任务模型 — `task_store.py`

**Files:**
- Create: `acad2pdf/task_store.py`

这是整个调度架构的核心数据层。所有任务、文件队列、Worker 注册表都集中在这里。

- [ ] **Step 1: 创建 task_store.py 基础结构**

```python
# acad2pdf/task_store.py
"""统一任务模型 + 内存队列 + Worker 注册表。"""

import logging
import os
import threading
import time
import uuid
import zipfile
from pathlib import Path

log = logging.getLogger("acad2pdf")


class FileItem:
    """任务中的单个文件。"""

    STATUS_PENDING = "pending"
    STATUS_ASSIGNED = "assigned"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"

    def __init__(self, file_id: str, name: str, source_path: str):
        self.id = file_id
        self.name = name
        self.source_path = source_path
        self.status = self.STATUS_PENDING
        self.assigned_to = None  # worker_id
        self.result = None
        self.error = None
        self.attempts = 0

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "error": self.error,
            "attempts": self.attempts,
        }


class Task:
    """统一任务（DWG→PDF 或 PDF→DWG）。"""

    def __init__(self, task_type: str, params: dict, results_dir: str):
        self.id = uuid.uuid4().hex[:12]
        self.type = task_type  # "dwg2pdf" | "pdf2dwg"
        self.status = "pending"
        self.files: list[FileItem] = []
        self.params = params
        self.results_dir = results_dir
        self.start_time = time.time()
        self.end_time = None
        self.zip_path = None
        self.zip_size_kb = None
        self.ok_count = 0
        self.total_time = None

    def add_file(self, name: str, source_path: str) -> FileItem:
        file_id = f"f{len(self.files)+1}_{uuid.uuid4().hex[:4]}"
        item = FileItem(file_id, name, source_path)
        self.files.append(item)
        return item

    @property
    def total(self):
        return len(self.files)

    @property
    def done_count(self):
        return sum(1 for f in self.files if f.status in (FileItem.STATUS_DONE, FileItem.STATUS_FAILED))

    def all_done(self):
        return all(f.status in (FileItem.STATUS_DONE, FileItem.STATUS_FAILED) for f in self.files)

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "total": self.total,
            "ok_count": self.ok_count,
            "done_count": self.done_count,
            "total_time": self.total_time,
            "zip_size_kb": self.zip_size_kb,
            "files": [f.to_dict() for f in self.files],
        }


class WorkerInfo:
    """注册的 Worker 节点。"""

    HEARTBEAT_TIMEOUT = 90  # 秒

    def __init__(self, worker_id: str, capacity: int):
        self.worker_id = worker_id
        self.capacity = capacity
        self.active_slots = 0
        self.done_count = 0
        self.total_time = 0.0
        self.avg_time = 0.0
        self.last_seen = time.time()
        self.registered_at = time.time()

    @property
    def online(self):
        return (time.time() - self.last_seen) < self.HEARTBEAT_TIMEOUT

    def touch(self, active_slots=None, done_count=None):
        self.last_seen = time.time()
        if active_slots is not None:
            self.active_slots = active_slots
        if done_count is not None:
            self.done_count = done_count

    def report_done(self, elapsed):
        self.done_count += 1
        self.total_time += elapsed
        self.avg_time = self.total_time / self.done_count

    def to_dict(self):
        return {
            "worker_id": self.worker_id,
            "status": "online" if self.online else "offline",
            "capacity": self.capacity,
            "active_slots": self.active_slots,
            "done_count": self.done_count,
            "avg_time": round(self.avg_time, 1),
            "last_seen": self.last_seen,
        }


class TaskStore:
    """全局任务存储 + 调度队列。"""

    MAX_RETRIES = 3

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._workers: dict[str, WorkerInfo] = {}
        self._lock = threading.Lock()

    # --- Task 管理 ---

    def create_task(self, task_type: str, params: dict, results_dir: str) -> Task:
        task = Task(task_type, params, results_dir)
        with self._lock:
            self._tasks[task.id] = task
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [t.to_dict() for t in self._tasks.values()]

    def start_task(self, task: Task):
        with self._lock:
            task.status = "running"

    def finish_task(self, task: Task):
        with self._lock:
            task.status = "done"
            task.end_time = time.time()
            task.total_time = round(task.end_time - task.start_time, 1)
            task.ok_count = sum(1 for f in task.files if f.status == FileItem.STATUS_DONE)

    def find_file(self, file_id: str) -> tuple[Task, FileItem] | None:
        """根据 file_id 查找所属任务和文件。"""
        for task in self._tasks.values():
            for f in task.files:
                if f.id == file_id:
                    return task, f
        return None

    # --- Worker 拉取 ---

    def pull(self, worker_id: str, capacity: int) -> list[dict]:
        """Worker 拉取任务文件，最多 capacity 个。"""
        result = []
        with self._lock:
            for task in self._tasks.values():
                if task.status != "running":
                    continue
                for f in task.files:
                    if f.status != FileItem.STATUS_PENDING:
                        continue
                    if len(result) >= capacity:
                        break
                    f.status = FileItem.STATUS_ASSIGNED
                    f.assigned_to = worker_id
                    f.attempts += 1
                    result.append({
                        "file_id": f.id,
                        "file_name": f.name,
                        "task_id": task.id,
                        "task_type": task.type,
                        "params": task.params,
                    })
                if len(result) >= capacity:
                    break
        return result

    def report_result(self, file_id: str, success: bool,
                      output_files: list[str] = None,
                      error: str = None, elapsed: float = 0.0,
                      metadata: dict = None):
        """Worker 上报转换结果。"""
        with self._lock:
            found = self.find_file(file_id)
            if not found:
                return False
            task, f = found
            if success:
                f.status = FileItem.STATUS_DONE
                f.result = metadata or {}
            else:
                if f.attempts < self.MAX_RETRIES:
                    f.status = FileItem.STATUS_PENDING
                    f.assigned_to = None
                    f.error = error
                else:
                    f.status = FileItem.STATUS_FAILED
                    f.error = error
            # 更新 Worker 统计
            worker = self._workers.get(f.assigned_to)
            if worker and success:
                worker.report_done(elapsed)
            # 检查任务是否全部完成
            if task.all_done():
                task.status = "done"
                task.end_time = time.time()
                task.total_time = round(task.end_time - task.start_time, 1)
                task.ok_count = sum(1 for ff in task.files if ff.status == FileItem.STATUS_DONE)
        return True

    # --- Worker 管理 ---

    def register_worker(self, worker_id: str, capacity: int) -> WorkerInfo:
        with self._lock:
            w = WorkerInfo(worker_id, capacity)
            self._workers[worker_id] = w
        log.info("Worker registered: %s (capacity=%d)", worker_id, capacity)
        return w

    def unregister_worker(self, worker_id: str):
        with self._lock:
            self._workers.pop(worker_id, None)
            # 回收 assigned 文件
            for task in self._tasks.values():
                for f in task.files:
                    if f.assigned_to == worker_id and f.status == FileItem.STATUS_ASSIGNED:
                        f.status = FileItem.STATUS_PENDING
                        f.assigned_to = None
        log.info("Worker unregistered: %s", worker_id)

    def heartbeat(self, worker_id: str, active_slots: int = None, done_count: int = None):
        with self._lock:
            w = self._workers.get(worker_id)
            if w:
                w.touch(active_slots, done_count)

    def recover_stale(self):
        """心跳超时的 Worker：assigned 文件回归 pending。"""
        with self._lock:
            for w in self._workers.values():
                if not w.online:
                    for task in self._tasks.values():
                        for f in task.files:
                            if f.assigned_to == w.worker_id and f.status == FileItem.STATUS_ASSIGNED:
                                f.status = FileItem.STATUS_PENDING
                                f.assigned_to = None
                                log.warning("Recovered file %s from stale worker %s", f.id, w.worker_id)

    def get_workers(self) -> list[dict]:
        with self._lock:
            return [w.to_dict() for w in self._workers.values()]

    # --- 清理 ---

    def reap_old_tasks(self, max_age: int = 3600):
        """清理超过 max_age 秒的已完成任务。"""
        import shutil
        now = time.time()
        with self._lock:
            expired = [
                tid for tid, t in self._tasks.items()
                if t.status == "done" and t.end_time and (now - t.end_time) > max_age
            ]
            for tid in expired:
                task = self._tasks.pop(tid)
                results_dir = task.results_dir
                if results_dir and os.path.isdir(results_dir):
                    shutil.rmtree(results_dir, ignore_errors=True)
                log.info("Reaped task %s", tid)


# 全局单例
store = TaskStore()
```

- [ ] **Step 2: Commit**

```bash
git add acad2pdf/task_store.py
git commit -m "feat: add unified task store with pull-based dispatch queue"
```

---

### Task 2: 调度端点 — `dispatch_routes.py`

**Files:**
- Create: `acad2pdf/dispatch_routes.py`

为 Worker 提供 `/dispatch/*` HTTP 端点。

- [ ] **Step 1: 创建 dispatch_routes.py**

```python
# acad2pdf/dispatch_routes.py
"""拉取式调度端点 — Worker 通过这些 API 拉取任务、回传结果。"""

import json
import logging
import os

from flask import Blueprint, request, jsonify, send_file
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
    # 为每个文件生成下载 URL
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

    # 保存上传的产出文件
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

    # 检查任务是否全部完成，触发 ZIP 打包
    if task.all_done():
        _finalize_task(task)

    return jsonify({"status": "ok"})


@dispatch_bp.route("/dispatch/file/<file_id>")
def download_source(file_id):
    """Worker 下载源文件（DWG/PDF）。"""
    err = _check_api_key()
    if err:
        return err
    found = store.find_file(file_id)
    if not found:
        return jsonify({"error": "file not found"}), 404
    task, f = found
    if not os.path.exists(f.source_path):
        return jsonify({"error": "source file not found on disk"}), 404
    return send_file(f.source_path, as_attachment=True,
                     download_name=f.name)


def _finalize_task(task):
    """任务全部完成：打 ZIP，广播 SSE。"""
    from .api import _sse_broadcast
    import zipfile

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
        total_pdfs = sum(
            len(os.listdir(os.path.join(task.results_dir, f.id)))
            for f in task.files
            if f.status == "done" and os.path.isdir(os.path.join(task.results_dir, f.id))
        )

    _sse_broadcast("task_done" if task.type == "dwg2pdf" else "pdf_task_done", {
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
    from .api import _check_api_key as _check_admin
    user = request.cookies.get("session")
    from flask import session
    if not session.get("user"):
        key = request.headers.get("X-API-Key") or request.args.get("apikey")
        from .api import API_KEY
        if not (API_KEY and key == API_KEY):
            return jsonify({"error": "unauthorized"}), 401
    store.recover_stale()
    return jsonify({"workers": store.get_workers()})
```

- [ ] **Step 2: Commit**

```bash
git add acad2pdf/dispatch_routes.py
git commit -m "feat: add dispatch routes for pull-based worker endpoints"
```

---

### Task 3: Worker 后台线程 — `worker.py`

**Files:**
- Create: `acad2pdf/worker.py`

Worker 的核心循环：pull → download → convert → upload result。支持本地和远程模式。

- [ ] **Step 1: 创建 worker.py**

```python
# acad2pdf/worker.py
"""Worker 后台线程 — pull→convert→result 循环。

本地模式：作为后台线程运行在主 API 进程内。
远程模式：独立进程，配置 master_url 连接主 API。
"""

import json
import logging
import os
import tempfile
import threading
import time
import uuid

import requests as http_req
from werkzeug.utils import secure_filename

log = logging.getLogger("acad2pdf")


class Worker:
    """拉取式 Worker。"""

    def __init__(self, worker_id: str, capacity: int, master_url: str,
                 api_key: str = "", acad_exe: str = "", timeout: int = 300):
        self.worker_id = worker_id
        self.capacity = capacity
        self.master_url = master_url.rstrip("/")
        self.api_key = api_key
        self.acad_exe = acad_exe
        self.timeout = timeout
        self._running = False
        self._active = 0
        self._done = 0
        self._lock = threading.Lock()

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _api(self, path):
        return f"{self.master_url}{path}"

    def register(self):
        try:
            resp = http_req.post(
                self._api("/dispatch/register"),
                json={"worker_id": self.worker_id, "capacity": self.capacity},
                headers=self._headers(), timeout=10,
            )
            if resp.status_code == 200:
                log.info("Worker %s registered to %s", self.worker_id, self.master_url)
            else:
                log.warning("Worker %s register failed: %s", self.worker_id, resp.text)
        except Exception as ex:
            log.error("Worker %s register error: %s", self.worker_id, ex)

    def unregister(self):
        try:
            http_req.post(
                self._api("/dispatch/unregister"),
                json={"worker_id": self.worker_id},
                headers=self._headers(), timeout=10,
            )
        except Exception:
            pass

    def heartbeat(self):
        try:
            http_req.post(
                self._api("/dispatch/heartbeat"),
                json={"worker_id": self.worker_id,
                      "active_slots": self._active, "done_count": self._done},
                headers=self._headers(), timeout=10,
            )
        except Exception:
            pass

    def pull(self) -> list[dict]:
        try:
            resp = http_req.post(
                self._api("/dispatch/pull"),
                json={"worker_id": self.worker_id, "capacity": 1},
                headers=self._headers(), timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("files", [])
        except Exception as ex:
            log.error("Worker %s pull error: %s", self.worker_id, ex)
        return []

    def download_file(self, file_id: str, dest_dir: str) -> str | None:
        try:
            resp = http_req.get(
                self._api(f"/dispatch/file/{file_id}"),
                headers=self._headers(), timeout=60, stream=True,
            )
            if resp.status_code != 200:
                return None
            cd = resp.headers.get("Content-Disposition", "")
            fname = "source_file"
            if "filename=" in cd:
                fname = cd.split("filename=")[-1].strip('" ')
            path = os.path.join(dest_dir, secure_filename(fname) or fname)
            with open(path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            return path
        except Exception as ex:
            log.error("Worker %s download error: %s", self.worker_id, ex)
            return None

    def report_result(self, file_id: str, success: bool,
                      output_dir: str = "", error: str = "",
                      elapsed: float = 0.0, metadata: dict = None):
        try:
            data = {
                "file_id": file_id,
                "success": str(success).lower(),
                "error": error,
                "elapsed": str(elapsed),
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            }
            files = []
            if success and output_dir and os.path.isdir(output_dir):
                for name in os.listdir(output_dir):
                    fpath = os.path.join(output_dir, name)
                    if os.path.isfile(fpath):
                        files.append(("files", (name, open(fpath, "rb"))))
            try:
                h = {}
                if self.api_key:
                    h["X-API-Key"] = self.api_key
                resp = http_req.post(
                    self._api("/dispatch/result"),
                    data=data, files=files, headers=h, timeout=120,
                )
                if resp.status_code != 200:
                    log.warning("Worker %s report failed: %s", self.worker_id, resp.text)
            finally:
                for _, (_, fh) in files:
                    fh.close()
        except Exception as ex:
            log.error("Worker %s report error: %s", self.worker_id, ex)

    def _convert_one(self, file_info: dict, local_path: str) -> dict:
        """根据 task_type 调用对应转换函数。"""
        task_type = file_info.get("task_type", "dwg2pdf")
        params = file_info.get("params", {})
        output_dir = os.path.dirname(local_path) + f"_out_{uuid.uuid4().hex[:4]}"
        os.makedirs(output_dir, exist_ok=True)
        t0 = time.time()

        try:
            if task_type == "dwg2pdf":
                from .converter import convert_dwg_lsp
                result = convert_dwg_lsp(
                    local_path, output_dir,
                    printer=params.get("printer"),
                    plot_style=params.get("plot_style"),
                    border_keywords=params.get("border_keywords"),
                    timeout=self.timeout,
                )
                elapsed = round(time.time() - t0, 1)
                return {
                    "success": result.success,
                    "elapsed": elapsed,
                    "output_dir": output_dir if result.success else "",
                    "error": result.error or "",
                    "metadata": {
                        "pdf_count": len(result.borders) if result.borders else (1 if result.success else 0),
                    },
                }
            else:  # pdf2dwg
                from .pdf2dwg_worker import convert_one_pdf
                work_dir = os.path.join(output_dir, "_work")
                os.makedirs(work_dir, exist_ok=True)
                result = convert_one_pdf(
                    local_path, output_dir, work_dir,
                    self.acad_exe or params.get("acad_exe", ""),
                    self.timeout,
                )
                elapsed = round(time.time() - t0, 1)
                return {
                    "success": result["ok"],
                    "elapsed": elapsed,
                    "output_dir": output_dir if result["ok"] else "",
                    "error": result.get("error", ""),
                    "metadata": {
                        "dwg_size_mb": round(result.get("dwg_size", 0) / 1024 / 1024, 2),
                    },
                }
        except Exception as ex:
            elapsed = round(time.time() - t0, 1)
            return {"success": False, "elapsed": elapsed, "output_dir": "",
                    "error": str(ex), "metadata": {}}

    def run_one(self, file_info: dict):
        """处理单个文件：download → convert → report。"""
        with self._lock:
            self._active += 1

        tmp_dir = tempfile.mkdtemp(prefix=f"worker_{self.worker_id}_")
        try:
            local_path = self.download_file(file_info["file_id"], tmp_dir)
            if not local_path:
                self.report_result(file_info["file_id"], False, error="download failed")
                return

            r = self._convert_one(file_info, local_path)
            self.report_result(
                file_info["file_id"], r["success"],
                r.get("output_dir", ""), r.get("error", ""),
                r.get("elapsed", 0), r.get("metadata"),
            )
            with self._lock:
                self._done += 1
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
            with self._lock:
                self._active = max(0, self._active - 1)

    def run_loop(self):
        """Worker 主循环（单线程拉取模式）。"""
        self._running = True
        log.info("Worker %s loop started (capacity=%d)", self.worker_id, self.capacity)
        while self._running:
            files = self.pull()
            if not files:
                time.sleep(2)
                continue
            for f in files:
                if not self._running:
                    break
                self.run_one(f)
        log.info("Worker %s loop stopped", self.worker_id)

    def stop(self):
        self._running = False


def start_worker_threads(worker: Worker, num_threads: int):
    """启动 Worker 的多线程循环。每个线程独立 pull→convert→result。"""
    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=worker.run_loop, daemon=True,
                             name=f"worker_{worker.worker_id}_{i}")
        t.start()
        threads.append(t)
    return threads
```

- [ ] **Step 2: Commit**

```bash
git add acad2pdf/worker.py
git commit -m "feat: add worker loop with pull-convert-result cycle"
```

---

### Task 4: 改造 api.py — `/convert` 改用 task_store

**Files:**
- Modify: `acad2pdf/api.py`

将 DWG→PDF 的 `/convert` 从直接 ThreadPoolExecutor 改为提交到 task_store。移除 `_tasks`、`_executor`、`_reap_tasks`。

- [ ] **Step 1: 修改 api.py**

改动要点：
1. 移除 `_tasks`、`_tasks_lock`、`_executor`
2. 导入 `store` from `task_store`
3. `/convert` 路由：创建 Task → 添加文件 → 启动任务 → 广播 `task_start`
4. `/task/<id>` 和 `/download/<id>` 从 store 读取
5. 移除 `_reap_tasks` 线程，改用 store 的 `reap_old_tasks`
6. 移除 `_progress_cb`（进度由 Worker 通过 SSE 广播）

在 `api.py` 顶部添加 import：

```python
from .task_store import store
```

移除以下变量和函数：
- `_tasks`、`_tasks_lock`（第87-88行）
- `_executor`（第95行）
- `_progress_cb`（第131-137行）
- `_reap_tasks`（第671-683行）
- `_TASK_TTL`（第668行）

替换 `/convert` 路由：

```python
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
    plot_style = request.form.get("plot_style", "").strip() or runtime_config["plot_style"]
    border_kw = request.form.get("border_keywords", "").strip() or runtime_config["border_keywords"]

    project_dir = os.path.dirname(os.path.dirname(__file__))
    task = store.create_task("dwg2pdf", {
        "printer": runtime_config["printer"],
        "plot_style": plot_style,
        "border_keywords": border_kw,
        "plot_scale": "Fit",
        "drawing_scale": runtime_config.get("drawing_scale", 1.0),
    }, results_dir=os.path.join(WORK_DIR or os.path.join(project_dir, "output"), task.id))

    os.makedirs(task.results_dir, exist_ok=True)
    upload_dir = os.path.join(task.results_dir, "upload")
    os.makedirs(upload_dir, exist_ok=True)

    for f in dwg_files:
        safe_name = secure_filename(f.filename) or f"{uuid.uuid4().hex[:8]}.dwg"
        path = os.path.join(upload_dir, safe_name)
        f.save(path)
        task.add_file(f.filename, path)

    store.start_task(task)

    _sse_broadcast("task_start", {"task_id": task.id, "total": task.total,
                                    "workers": runtime_config["max_workers"]})
    log.info("Task %s: %d DWG files queued", task.id, task.total)

    return jsonify({"task_id": task.id, "status": "running", "total": task.total})
```

注意：`task` 变量需要在 `create_task` 之前就确定 results_dir，所以 results_dir 用 task_id 预先构造。修正：先用 uuid 生成 id，再创建 Task。改为：

```python
    task_id = uuid.uuid4().hex[:12]
    results_dir = os.path.join(
        WORK_DIR or os.path.join(project_dir, "output"), task_id)
    task = store.create_task("dwg2pdf", {
        "printer": runtime_config["printer"],
        "plot_style": plot_style,
        "border_keywords": border_kw,
        "plot_scale": "Fit",
        "drawing_scale": runtime_config.get("drawing_scale", 1.0),
    }, results_dir=results_dir)
```

替换 `/task/<id>`：

```python
@app.route("/task/<task_id>")
def get_task(task_id):
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task.to_dict())
```

替换 `/download/<id>`：

```python
@app.route("/download/<task_id>")
def download(task_id):
    task = store.get_task(task_id)
    if not task or not task.zip_path or not os.path.exists(task.zip_path):
        return jsonify({"error": "no result"}), 404
    return send_file(task.zip_path, as_attachment=True, download_name=f"{task_id}.zip")
```

替换 `/tasks`：

```python
@app.route("/tasks")
def list_tasks():
    return jsonify(store.list_tasks())
```

添加定期清理和心跳回收线程（在 `if __name__ == "__main__"` 之前）：

```python
def _maintenance_loop():
    while True:
        time.sleep(300)
        store.reap_old_tasks()
        store.recover_stale()

threading.Thread(target=_maintenance_loop, daemon=True).start()
```

- [ ] **Step 2: Commit**

```bash
git add acad2pdf/api.py
git commit -m "refactor: convert api.py to use unified task store"
```

---

### Task 5: 改造 pdf2dwg_api.py — `/convert-pdf` 改用 task_store

**Files:**
- Modify: `acad2pdf/pdf2dwg_api.py`

和 api.py 类似，将 PDF→DWG 的任务提交改为 task_store。

- [ ] **Step 1: 修改 pdf2dwg_api.py**

改动要点：
1. 移除 `_pdf_tasks`、`_pdf_tasks_lock`
2. 导入 `store`
3. `/convert-pdf` 路由：创建 Task → 添加文件 → 启动任务
4. `/pdf-task/<id>` 和 `/download-pdf-zip/<id>` 从 store 读取
5. 移除 `_dispatch` 线程和 DispatchSession 导入

替换 `/convert-pdf` 路由：

```python
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
```

替换 `/pdf-task/<id>`：

```python
@pdf2dwg_bp.route("/pdf-task/<task_id>")
def get_pdf_task(task_id):
    task = store.get_task(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    return jsonify(task.to_dict())
```

替换 `/download-pdf-zip/<id>`：

```python
@pdf2dwg_bp.route("/download-pdf-zip/<task_id>")
def download_pdf_zip(task_id):
    task = store.get_task(task_id)
    if not task or not task.zip_path or not os.path.exists(task.zip_path):
        return jsonify({"error": "no result"}), 404
    return send_file(task.zip_path, as_attachment=True, download_name=f"pdf2dwg_{task_id}.zip")
```

替换 `/pdf-tasks`：

```python
@pdf2dwg_bp.route("/pdf-tasks")
def list_pdf_tasks():
    return jsonify(store.list_tasks())
```

移除 `/convert-pdf-single`（不再需要推送式 Worker 端点）和 `/download-pdf/<task_id>/<filename>`。

- [ ] **Step 2: Commit**

```bash
git add acad2pdf/pdf2dwg_api.py
git commit -m "refactor: convert pdf2dwg_api to use unified task store"
```

---

### Task 6: 改造 run.py — 注册 Blueprint + 启动本地 Worker

**Files:**
- Modify: `run.py`

- [ ] **Step 1: 修改 run.py**

```python
"""
ACADxPDF 启动入口 — 统一调度架构（DWG→PDF + PDF→DWG）。

注册所有 Blueprint，启动本地 Worker 后台线程。
"""

import os
import threading
from flask import send_from_directory
from acad2pdf.api import app, API_HOST, API_PORT, MAX_WORKERS, log
from acad2pdf.pdf2dwg_api import pdf2dwg_bp
from acad2pdf.dispatch_routes import dispatch_bp
from acad2pdf.worker import Worker, start_worker_threads

# 注册 Blueprint
app.register_blueprint(pdf2dwg_bp)
app.register_blueprint(dispatch_bp)


# 用 before_request 覆盖 / 路由，返回新版前端
@app.before_request
def _override_index():
    from flask import request
    if request.path == "/":
        static_dir = os.path.join(os.path.dirname(__file__), "acad2pdf", "static")
        return send_from_directory(static_dir, "index2.html")


def start_local_worker():
    """启动本地 Worker（后台线程）。"""
    from acad2pdf.api import API_KEY, runtime_config
    worker = Worker(
        worker_id="local",
        capacity=runtime_config.get("max_workers", MAX_WORKERS),
        master_url=f"http://127.0.0.1:{API_PORT}",
        api_key=API_KEY,
        acad_exe=os.environ.get("ACAD_EXE", r"C:\opt\AutoCAD 2026\acad.exe"),
        timeout=runtime_config.get("timeout", 300),
    )
    worker.register()
    threads = start_worker_threads(worker, worker.capacity)

    # 心跳线程
    def heartbeat_loop():
        import time
        while True:
            time.sleep(30)
            worker.heartbeat()

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    return worker, threads


if __name__ == "__main__":
    log.info("ACADxPDF starting on %s:%s (workers=%d) [unified dispatch]",
             API_HOST, API_PORT, MAX_WORKERS)
    worker, _ = start_local_worker()
    log.info("Local worker started (capacity=%d)", worker.capacity)
    app.run(host=API_HOST, port=API_PORT, threaded=True)
```

- [ ] **Step 2: Commit**

```bash
git add run.py
git commit -m "feat: register dispatch blueprint and start local worker in run.py"
```

---

### Task 7: 清空旧 dispatcher.py

**Files:**
- Modify: `acad2pdf/dispatcher.py`

- [ ] **Step 1: 替换 dispatcher.py 内容**

旧的 SWRR 推送调度器不再使用。保留文件但内容替换为空壳（避免 import 报错）：

```python
# acad2pdf/dispatcher.py
"""
调度器（已废弃）。

调度逻辑已迁移到 task_store.py + dispatch_routes.py + worker.py。
本文件保留以避免 import 错误，将在后续版本移除。
"""
```

- [ ] **Step 2: Commit**

```bash
git add acad2pdf/dispatcher.py
git commit -m "chore: deprecate old SWRR dispatcher (replaced by pull-based dispatch)"
```

---

### Task 8: 验证 + 集成测试

**Files:**
- No new files

- [ ] **Step 1: 启动服务器验证**

```bash
python run.py
```

预期输出：
```
ACADxPDF starting on 0.0.0.0:5557 (workers=4) [unified dispatch]
Local worker started (capacity=4)
Worker local registered to http://127.0.0.1:5557
```

- [ ] **Step 2: 验证 /convert 端点**

```bash
curl -X POST http://localhost:5557/convert -F "files=@test.dwg" -H "X-API-Key: axp-xxx"
```

预期：`{"task_id": "xxx", "status": "running", "total": 1}`

- [ ] **Step 3: 验证 Worker 拉取**

```bash
curl -X POST http://localhost:5557/dispatch/pull -H "X-API-Key: axp-xxx" -H "Content-Type: application/json" -d '{"worker_id":"test","capacity":1}'
```

预期：`{"files": [{"file_id": "f1_xxx", "file_name": "test.dwg", ...}]}`

- [ ] **Step 4: 验证 Worker 状态**

```bash
curl http://localhost:5557/admin/workers
```

预期：`{"workers": [{"worker_id": "local", "status": "online", ...}]}`

- [ ] **Step 5: 验证 UI 前端**

浏览器打开 `http://localhost:5557`，确认：
- DWG→PDF 上传转换正常
- PDF→DWG 上传转换正常
- SSE 进度正常推送
- Worker 状态在管理页面可见

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "test: verify unified dispatch architecture"
```
