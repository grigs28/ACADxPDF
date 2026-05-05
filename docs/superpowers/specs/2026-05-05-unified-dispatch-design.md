# 统一调度架构设计：拉取式 Worker + 统一任务模型

## 概述

将 DWG→PDF 和 PDF→DWG 两条转换线统一到同一个调度架构下。主 API 同时作为调度中心和文件服务，Worker 通过 HTTP 拉取任务、回传结果。外部程序和前端的调用接口不变。

## 设计决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 调度模式 | Worker 拉取 | 天然负载均衡，Worker 随时上下线 |
| 认证方式 | API Key | 和现有机制一致 |
| 进程模型 | 主 API + Worker 同一进程 | 单机部署简单，多机时 Worker 独立启动 |
| 拉取粒度 | 每次 1~capacity 个文件 | 按线程数拉取，细粒度均衡 |
| 文件传输 | HTTP POST 回传 | 不依赖共享目录 |
| 队列存储 | 内存 | 简单可靠，重启丢失可接受 |
| 现有 dispatcher.py | 重写为拉取模式 | 统一架构，一套代码两条线 |

## 架构

```
外部程序 / 前端              主 API (Flask)              Worker (同一进程或远程)
    |                          |                            |
    | POST /convert            |                            |
    | files + params           |                            |
    |------------------------->|                            |
    | {task_id}                |                            |
    |                          |  文件入统一任务队列           |
    |                          |                            |
    | GET /task/<id>           |     POST /dispatch/pull    |
    |------------------------->|<---------------------------|
    | {status, progress}       |  返回 1~N 个任务文件         |
    |                          |                            |
    |                          |     Worker 本地转换          |
    |                          |     (acad.exe + LSP)        |
    |                          |                            |
    |                          |     POST /dispatch/result   |
    |                          |<---------------------------|
    |                          |  multipart: 元数据 + 产出文件|
    |                          |                            |
    | GET /download/<id>       |                            |
    |------------------------->|                            |
    | ← ZIP                    |                            |
```

## 统一任务模型

### 任务数据结构

```python
task = {
    "id": "abc123",
    "type": "dwg2pdf" | "pdf2dwg",
    "status": "pending" | "running" | "done",
    "total": 12,
    "files": [
        {
            "id": "f1",
            "name": "xxx.dwg",
            "status": "pending" | "assigned" | "done" | "failed",
            "assigned_to": None,        # worker_id
            "source_path": "/path/to/xxx.dwg",
            "result": None,             # 上报的结果元数据
            "error": None,
            "attempts": 0,
        },
    ],
    "params": { ... },                 # 转换参数（printer, plot_style 等）
    "results_dir": "/path/to/output",
    "start_time": ...,
    "end_time": None,
    "zip_path": None,
    "zip_size_kb": None,
    "ok_count": 0,
    "total_time": None,
}
```

### Worker 拉取

Worker 不关心任务边界，只看文件。一个 Worker 可能同时处理来自不同任务的文件。

```
POST /dispatch/pull
Body: { worker_id: "node-A", capacity: 4 }
→ 从所有 running 任务中找 pending 文件
→ 最多返回 capacity 个，标记为 assigned
→ 返回 [{ file_id, file_name, task_type, params, download_url }]
```

### Worker 上报

```
POST /dispatch/result
Body: multipart (metadata JSON + 产出文件)
→ 找到文件记录，标记 done/failed
→ 存储产出文件到 results_dir
→ 该任务所有文件完成则标记 done，打 ZIP
```

## Worker 管理

### Worker 配置（worker.json）

```json
{
  "master_url": "http://192.168.0.5:5557",
  "worker_id": "node-A",
  "capacity": 4,
  "api_key": "axp-xxx",
  "acad_exe": "C:\\opt\\AutoCAD 2026\\acad.exe",
  "timeout": 300
}
```

Worker 自己配置主 API 地址、ID 和线程数。

### 心跳机制

| 动作 | 端点 | 说明 |
|------|------|------|
| 注册 | `POST /dispatch/register` | 启动时调用，`{worker_id, capacity}` |
| 心跳 | `POST /dispatch/heartbeat` | 每 30 秒，`{worker_id, active_slots, done_count}` |
| 下线 | `POST /dispatch/unregister` | 退出时调用，assigned 文件回归 pending |

### 在线判定

- 超过 90 秒未心跳 → 自动标记 offline
- 离线 Worker 的 assigned 文件自动回归 pending 队列（可重试）
- `GET /admin/workers` 返回所有 Worker 状态（在线/离线、活跃数、完成数、平均耗时）

### 本地 Worker

主 API 启动时自动注册一个本地 Worker（worker_id="local"），后台线程循环 pull→convert→result。单机部署无需额外进程，行为与现在一致。本地 Worker 的 capacity 取自 `runtime_config["max_workers"]`。

## 端点汇总

### 外部调用（不变）

| 端点 | 方法 | 用途 |
|------|------|------|
| `/convert` | POST | 上传 DWG，返回 task_id |
| `/convert-pdf` | POST | 上传 PDF，返回 task_id |
| `/task/<id>` | GET | 查询任务状态 |
| `/download/<id>` | GET | 下载结果 ZIP |
| `/stream` | GET | SSE 实时进度 |
| `/config` | GET/POST | 配置管理 |

### Worker 调用（新增）

| 端点 | 方法 | 用途 |
|------|------|------|
| `/dispatch/register` | POST | Worker 注册 |
| `/dispatch/heartbeat` | POST | Worker 心跳 |
| `/dispatch/unregister` | POST | Worker 下线 |
| `/dispatch/pull` | POST | 拉取任务文件 |
| `/dispatch/result` | POST | 上报转换结果 |
| `/dispatch/file/<file_id>` | GET | 下载源文件 |

### 管理端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/admin/workers` | GET | 查看 Worker 在线状态 |
| 现有管理端点 | — | 保持不变 |

## 文件组织

```
_work/
  <task_id>/
    upload/              ← 原始上传文件
      f1_xxx.dwg
      f2_yyy.pdf
    output/              ← Worker 回传的结果
      f1_xxx/
        xxx_图框1.pdf
        xxx_图框2.pdf
      f2_yyy/
        yyy_output.dwg
    result.zip           ← 全部完成后打包
```

Worker 通过 `GET /dispatch/file/<file_id>` 下载源文件到本地临时目录，转换完成后通过 `POST /dispatch/result` 回传产出文件。主 API 收到后存入对应 `output/fN/` 目录。

## Worker 转换逻辑

Worker 内部用 ThreadPoolExecutor，每个线程循环：

```
while running:
    files = POST /dispatch/pull { worker_id, capacity: 1 }
    if no files:
        sleep(2); continue
    for file in files:
        dwg = GET /dispatch/file/<file_id>
        result = convert_local(dwg)       # acad.exe + LSP
        POST /dispatch/result (result + PDFs)
```

DWG→PDF 调用现有的 `convert_dwg_lsp()`，PDF→DWG 调用现有的 `convert_one_pdf()`。Worker 端转换逻辑复用现有代码，不重写。

## 错误处理

| 场景 | 处理 |
|------|------|
| 单文件转换失败 | 标记 failed，attempts++，最多重试 3 次 |
| Worker 心跳超时 | assigned 文件回归 pending，其他 Worker 可接手 |
| 主 API 重启 | 内存队列丢失，进行中的任务标记为 failed |
| Worker 重启 | 已 assigned 但未上报的文件由心跳超时机制回收 |
| ZIP 打包 | 全部文件 done/failed 后触发，单个文件失败不影响其他 |

## 与现有代码的关系

| 现有模块 | 处理 |
|----------|------|
| `converter.py` | 不修改，Worker 调用 `convert_dwg_lsp()` |
| `pdf2dwg_worker.py` | 不修改，Worker 调用 `convert_one_pdf()` |
| `dispatcher.py` | 重写，从 SWRR 推送改为拉取式队列 |
| `api.py` | 改造，任务管理从独立字典改为统一任务模型，新增 `/dispatch/*` 端点 |
| `pdf2dwg_api.py` | 合并入 api.py 的统一任务模型，独立 Blueprint 可保留 |
| `workers.json` | 改为 `worker.json`，Worker 端配置 |
| `.env` | 新增 `MASTER_URL`、`WORKER_ID`、`WORKER_CAPACITY` |

## SSE 事件（不变）

| 事件 | 数据 | 触发时机 |
|------|------|----------|
| `task_start` | {task_id, total, workers} | 任务创建 |
| `file_start` | {task_id, file} | 文件被 Worker 拉取 |
| `progress` | {task_id, file, step} | 转换进度 |
| `file_done` | {task_id, file, success, pdf_count, elapsed} | Worker 上报结果 |
| `task_done` | {task_id, total_time, ok_count, total, total_pdfs} | 所有文件完成 |
