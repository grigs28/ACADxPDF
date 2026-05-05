# acad2pdf/worker.py
"""Worker 后台线程 — pull→convert→result 循环。

本地模式：作为后台线程运行在主 API 进程内。
远程模式：独立进程，配置 master_url 连接主 API。
"""

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid

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
        import requests as http_req
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
        import requests as http_req
        try:
            http_req.post(
                self._api("/dispatch/unregister"),
                json={"worker_id": self.worker_id},
                headers=self._headers(), timeout=10,
            )
        except Exception:
            pass

    def heartbeat(self):
        import requests as http_req
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
        import requests as http_req
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
        import requests as http_req
        try:
            h = {}
            if self.api_key:
                h["X-API-Key"] = self.api_key
            resp = http_req.get(
                self._api(f"/dispatch/file/{file_id}"),
                headers=h, timeout=60, stream=True,
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
        import requests as http_req
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
