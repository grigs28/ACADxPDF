# acad2pdf/task_store.py
"""统一任务模型 + 内存队列 + Worker 注册表。"""

import logging
import os
import shutil
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
        self.assigned_to = None
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
        self.type = task_type
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

    HEARTBEAT_TIMEOUT = 90

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
        for task in self._tasks.values():
            for f in task.files:
                if f.id == file_id:
                    return task, f
        return None

    def pull(self, worker_id: str, capacity: int) -> list[dict]:
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
            worker = self._workers.get(f.assigned_to)
            if worker and success:
                worker.report_done(elapsed)
            if task.all_done():
                task.status = "done"
                task.end_time = time.time()
                task.total_time = round(task.end_time - task.start_time, 1)
                task.ok_count = sum(1 for ff in task.files if ff.status == FileItem.STATUS_DONE)
        return True

    def register_worker(self, worker_id: str, capacity: int) -> WorkerInfo:
        with self._lock:
            w = WorkerInfo(worker_id, capacity)
            self._workers[worker_id] = w
        log.info("Worker registered: %s (capacity=%d)", worker_id, capacity)
        return w

    def unregister_worker(self, worker_id: str):
        with self._lock:
            self._workers.pop(worker_id, None)
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

    def reap_old_tasks(self, max_age: int = 3600):
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


store = TaskStore()
