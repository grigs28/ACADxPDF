# ACADxPDF API 说明文档

## 概述

ACADxPDF 是批量 DWG↔PDF 双向转换服务，提供 Web UI 和 REST API 两种使用方式。

- **DWG→PDF**：上传 DWG 文件，自动检测图框，输出分页 PDF（支持多线程并发）
- **PDF→DWG**：上传 PDF 文件，通过 AutoCAD PDFIMPORT 反向转换为 DWG

两种转换共用统一的拉取式 Worker 调度系统，支持多机分布式。

## 启动

```bash
python run.py
```

默认监听 `0.0.0.0:5557`，通过 `.env` 中 `API_HOST` / `API_PORT` 修改。

## 认证

系统支持两种认证方式，**二选一**即可：

| 方式 | 适用场景 | 说明 |
|------|----------|------|
| SSO 登录 | 本机浏览器使用 Web UI | 通过统一登录平台登录，浏览器自动跳转 |
| API Key | 其他电脑 / 脚本 / 程序调用 | 请求头 `X-API-Key` 或参数 `?apikey=xxx` |

> Web UI（`/`）必须通过 SSO 登录访问；所有 API 端点支持 SSO 或 API Key（任选一种）。

### API Key

首次启动自动生成，格式 `axp-{32位hex}`，存储在 `.env` 的 `API_KEY` 字段。

```bash
# 查看当前 API Key
cat .env | grep API_KEY
```

### 其他电脑调用

其他机器只需能网络访问主 API 即可，不需要 SSO，不需要浏览器。所有转换、查询、下载接口均通过 API Key 认证：

```bash
# 上传转换（DWG→PDF）
curl -X POST http://192.168.0.5:5557/convert \
  -H "X-API-Key: axp-xxxxxxxx" \
  -F "files=@图纸1.dwg" \
  -F "files=@图纸2.dwg"

# 查询任务状态
curl -H "X-API-Key: axp-xxxxxxxx" http://192.168.0.5:5557/task/a1b2c3d4e5f6

# 下载结果
curl -H "X-API-Key: axp-xxxxxxxx" -o result.zip http://192.168.0.5:5557/download/a1b2c3d4e5f6

# PDF→DWG 转换
curl -X POST http://192.168.0.5:5557/convert-pdf \
  -H "X-API-Key: axp-xxxxxxxx" \
  -F "files=@文件1.pdf"

# 也可以用 URL 参数传 API Key
curl http://192.168.0.5:5557/task/a1b2c3d4e5f6?apikey=axp-xxxxxxxx
```

> `192.168.0.5` 替换为主 API 机器的实际 IP。

---

## 接口列表

### DWG→PDF 转换

#### 上传转换

```
POST /convert
Content-Type: multipart/form-data
认证：API Key
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| files | file[] | 是 | DWG 文件，支持多个同时上传 |
| plot_style | string | 否 | 打印样式表（如 `monochrome.ctb`），默认使用配置值 |
| border_keywords | string | 否 | 图框块名关键词（逗号分隔），默认使用配置值 |

**响应（立即返回）**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "running",
  "total": 3
}
```

**示例**

```bash
# 单文件
curl -X POST http://localhost:5557/convert \
  -H "X-API-Key: axp-xxxxxxxx" \
  -F "files=@图纸1.dwg"

# 多文件 + 指定打印样式
curl -X POST http://localhost:5557/convert \
  -H "X-API-Key: axp-xxxxxxxx" \
  -F "files=@图纸1.dwg" \
  -F "files=@图纸2.dwg" \
  -F "plot_style=monochrome.ctb"
```

#### 查询任务状态

```
GET /task/<task_id>
认证：API Key
```

```json
{
  "id": "a1b2c3d4e5f6",
  "type": "dwg2pdf",
  "status": "done",
  "total": 3,
  "ok_count": 3,
  "done_count": 3,
  "total_time": 52.1,
  "zip_size_kb": 1024.5,
  "files": [
    {"id": "f1_aba7", "name": "图纸1.dwg", "status": "done", "assigned_to": "local"},
    {"id": "f2_fe7c", "name": "图纸2.dwg", "status": "done", "assigned_to": "local"}
  ]
}
```

#### 列出所有任务

```
GET /tasks
认证：无
```

#### 下载结果 ZIP

```
GET /download/<task_id>
认证：API Key
```

ZIP 包内容（按原始文件名分组）：

```
result.zip
├── 图纸1/
│   ├── 图纸1.dwg              ← 原始 DWG
│   ├── 图纸1_001_A3.pdf
│   ├── 图纸1_002_A3.pdf
│   └── _input_xxxxx.dxf       ← DXF 中间文件
└── 图纸2/
    ├── 图纸2.dwg
    └── 图纸2_001_A1.pdf
```

---

### PDF→DWG 转换

#### 批量转换

```
POST /convert-pdf
Content-Type: multipart/form-data
认证：API Key 或 SSO 登录
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| files | file[] | 是 | PDF 文件，支持多个 |

```bash
curl -X POST http://localhost:5557/convert-pdf \
  -H "X-API-Key: axp-xxxxxxxx" \
  -F "files=@test1.pdf" \
  -F "files=@test2.pdf"
```

响应格式同 DWG→PDF（返回 `task_id`，后台调度执行）。

**响应**

```json
{"task_id": "f6e5d4c3b2a1", "status": "running", "total": 2}
```

#### 追加文件到运行中的任务

```
POST /convert-pdf/add/<task_id>
Content-Type: multipart/form-data
认证：API Key 或 SSO 登录
```

#### PDF 任务状态

```
GET /pdf-task/<task_id>
认证：API Key 或 SSO 登录
```

#### 列出 PDF 任务

```
GET /pdf-tasks
认证：API Key 或 SSO 登录
```

#### 下载 PDF→DWG 结果

```
GET /download-pdf-zip/<task_id>
认证：API Key 或 SSO 登录
```

```bash
curl -O http://localhost:5557/download-pdf-zip/f6e5d4c3b2a1 -H "X-API-Key: axp-xxxxxxxx"
```

---

### 打印样式管理

#### 列出打印样式

```
GET /plot-styles
认证：无
```

```json
{"files": ["monochrome.ctb", "custom.ctb"], "default": "monochrome.ctb"}
```

#### 上传打印样式

```
POST /plot-styles/upload
Content-Type: multipart/form-data
认证：无
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | file | 是 | .ctb 文件 |

#### 删除打印样式

```
DELETE /plot-styles/<name>
认证：无
```

至少保留一个 CTB 文件，不允许全部删除。

---

### 实时进度（SSE）

```
GET /stream
认证：无
```

Server-Sent Events 流，事件类型：

| 事件 | 说明 |
|------|------|
| `connected` | 连接建立 |
| `heartbeat` | 心跳（30 秒间隔） |
| `task_start` | 任务开始 |
| `file_done` | 单文件转换完成 |
| `task_done` | DWG→PDF 任务全部完成 |
| `pdf_task_start` | PDF 任务开始 |
| `pdf_task_done` | PDF→DWG 任务全部完成 |
| `pdf_task_add` | PDF 任务追加文件 |

**示例**

```javascript
const es = new EventSource('/stream');
es.addEventListener('file_done', (e) => {
  const data = JSON.parse(e.data);
  console.log(data.file, data.success, data.elapsed);
});
```

---

### 配置管理

#### 获取运行配置

```
GET /config
认证：无
```

```json
{
  "printer": "DWG To PDF.pc3",
  "plot_style": "monochrome.ctb",
  "timeout": 600,
  "border_keywords": "TK,TUKUANG,BORDER,FRAME,TITLE",
  "merge_borders": false,
  "auto_paper_size": true,
  "split_borders": true,
  "max_workers": 6,
  "drawing_scale": 1.0
}
```

#### 修改运行配置

```
POST /config
Content-Type: application/json
认证：需 SSO 登录
```

| 可修改字段 | 类型 | 说明 |
|------------|------|------|
| printer | string | 打印机名称 |
| plot_style | string | 打印样式表 |
| timeout | int | 单文件超时（秒） |
| border_keywords | string | 图框块名关键字（逗号分隔） |
| merge_borders | bool | 合并相邻图框 |
| auto_paper_size | bool | 自动匹配纸幅 |
| split_borders | bool | 分割图框 |
| max_workers | int | 最大线程数 |

#### 系统管理配置（仅管理员）

```
GET  /admin/config    # 查看系统配置
POST /admin/config    # 修改系统配置（写入 .env）
认证：需 SSO 登录且 is_admin=1
```

| 字段 | 说明 |
|------|------|
| api_key | API 密钥 |
| acad_path | accoreconsole.exe 路径 |
| acad_exe | acad.exe 路径 |
| tarch_arx | 天正 ARX 插件路径 |
| acad_template | 模板 DWG 路径 |
| work_dir | 工作目录 |
| workers | 远程 Worker 列表 |
| pdf_timeout | PDF→DWG 超时 |

#### 清理临时文件（仅管理员）

```
POST /admin/clean
认证：需 SSO 登录且 is_admin=1
```

清理 `_work/` 和 `output/` 目录。

#### Worker 状态

```
GET /admin/workers
认证：SSO 登录 或 API Key
```

```json
{
  "workers": [
    {
      "worker_id": "local",
      "status": "online",
      "capacity": 4,
      "active_slots": 2,
      "done_count": 10,
      "avg_time": 45.2,
      "last_seen": 1714905600.0
    }
  ]
}
```

---

### 调度端点（Worker 使用）

Worker 通过以下端点与主 API 通信：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/dispatch/register` | POST | Worker 注册 |
| `/dispatch/unregister` | POST | Worker 注销 |
| `/dispatch/heartbeat` | POST | 心跳（30秒间隔） |
| `/dispatch/pull` | POST | 拉取待处理文件 |
| `/dispatch/result` | POST | 回传转换结果（multipart） |
| `/dispatch/file/<file_id>` | GET | 下载源文件 |

所有调度端点需要 API Key 认证。

---

### 其他接口

| 接口 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/` | GET | SSO | Web UI 首页 |
| `/health` | GET | 无 | 健康检查 `{"status":"ok","workers":4}` |
| `/auth/check` | GET | 无 | 检查登录状态 |
| `/logout` | GET | 无 | 退出登录 |
| `/callback` | GET | 无 | SSO ticket 回调（自动处理） |
| `/logs?lines=200` | GET | 无 | 获取最近日志 |

---

## SSE 事件数据格式

### task_start
```json
{"task_id": "xxx", "total": 3, "workers": 4}
```

### file_done
```json
{
  "task_id": "xxx", "file_id": "f1_aba7", "file": "图纸1.dwg",
  "success": true, "elapsed": 45.2,
  "done_count": 1, "total": 3,
  "metadata": {"pdf_count": 2}
}
```

### task_done（DWG→PDF）
```json
{
  "task_id": "xxx", "total_time": 52.1,
  "ok_count": 3, "total": 3, "total_pdfs": 5,
  "zip_size_kb": 1024.5
}
```

### pdf_task_done（PDF→DWG）
```json
{
  "task_id": "xxx", "total_time": 120.3,
  "ok_count": 5, "total": 5, "zip_size_kb": 2048.0
}
```

---

## 多机部署

### 主 API 机器

正常启动 `python run.py`，本地 Worker 自动作为后台线程运行。

### 远程 Worker 机器

1. 安装 Python 依赖和 AutoCAD
2. 创建 `worker.json`：

```json
{
  "master_url": "http://192.168.0.5:5557",
  "worker_id": "node-A",
  "capacity": 4,
  "api_key": "axp-xxxxxxxx",
  "acad_exe": "C:\\opt\\AutoCAD 2026\\acad.exe",
  "timeout": 300
}
```

3. 启动 Worker：

```bash
python -m acad2pdf.worker
```

### Worker 生命周期

1. 注册 → `POST /dispatch/register`
2. 拉取任务 → `POST /dispatch/pull`（每次拉取 capacity 个文件）
3. 下载源文件 → `GET /dispatch/file/<id>`
4. 本地转换（调用 AutoCAD）
5. 上传结果 → `POST /dispatch/result`（multipart，含所有输出文件）
6. 心跳 → `POST /dispatch/heartbeat`（30 秒间隔）

- Worker 90 秒无心跳自动标记离线，其已分配文件回收到队列重新分配
- 转换失败的文件自动重试（最多 3 次）
- 管理后台可查看所有 Worker 在线状态

---

## 配置文件

### .env

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| ACAD_PATH | `C:\Autodesk\AutoCAD 2020\accoreconsole.exe` | accoreconsole 路径 |
| ACAD_EXE | `C:\opt\AutoCAD 2026\acad.exe` | acad.exe 路径 |
| TARCH_ARX | `C:\opt\T30-PlugInV1.0\sys25x64\tch_kernal.arx` | 天正 ARX 插件路径 |
| ACAD_TEMPLATE | `C:\opt\ACADxPDF\Template\mt.dwg` | 模板 DWG 路径 |
| WORK_DIR | 空（使用 `_work/`） | 工作目录 |
| PRINTER | `DWG To PDF.pc3` | 打印机配置 |
| PLOT_STYLE | `monochrome.ctb` | 打印样式 |
| TIMEOUT | `600` | 单文件超时（秒） |
| BORDER_KEYWORDS | `TK,TUKUANG,BORDER,FRAME,TITLE` | 图框块名关键字 |
| MAX_WORKERS | `6` | Worker 线程数 |
| API_HOST | `0.0.0.0` | 监听地址 |
| API_PORT | `5557` | 监听端口 |
| API_KEY | 自动生成 | API 密钥 |
| SSO_URL | `http://192.168.0.8:80` | SSO 登录平台地址 |

### worker.json（远程 Worker）

详见上方「多机部署」章节。

---

## 权限说明

| 角色 | 权限 |
|------|------|
| 未登录 | 仅可访问 `/health`、`/stream`、`/auth/check`、`/callback`、`/logout`、`/logs`、`/plot-styles` |
| SSO 登录用户 | 使用 Web UI、调用所有转换接口、修改打印参数、查看配置和任务 |
| 管理员 (`is_admin=1`) | 额外可修改系统配置（API Key、CAD 路径、Worker 列表等） |
| API Key | 可调用所有转换、下载、任务查询和调度接口 |

---

## 图框检测

1. **块名匹配**（优先）— 查找 INSERT 块名称含 `BORDER_KEYWORDS` 中关键字的块参照
2. **矩形检测**（兜底）— 扫描封闭 LWPOLYLINE 矩形和 LINE 围合矩形

## 天正（TArch）支持

- 自动通过 `/ld` 参数加载天正 ARX 插件
- 使用 `acad.exe` 启动，通过模板 DWG 避免弹窗

## 注意事项

- 任务完成后保留 1 小时，超时自动清理（含上传文件和输出结果）
- 转换完成后自动清理 `_work` 临时目录
- 中文文件名完全支持（内部自动转换为 ASCII 安全名称处理）
- 日志输出到 `logs/api.log`，按 20MB 轮转，保留 5 份
