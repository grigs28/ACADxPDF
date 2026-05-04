# ACADxPDF API 说明文档

## 概述

ACADxPDF 是批量 DWG↔PDF 双向转换服务，提供 Web UI 和 REST API 两种使用方式。

- **DWG→PDF**：上传 DWG 文件，自动检测图框，输出分页 PDF（支持多线程并发）
- **PDF→DWG**：上传 PDF 文件，通过 AutoCAD PDFIMPORT 反向转换为 DWG（支持多机分布式调度）

## 启动

```bash
python -m acad2pdf.api
```

默认监听 `0.0.0.0:5557`，通过 `.env` 中 `API_HOST` / `API_PORT` 修改。

## 认证

系统支持两种认证方式，二选一即可：

| 方式 | 适用场景 | 说明 |
|------|----------|------|
| SSO 登录 | Web 浏览器使用 | 通过统一登录平台（yz-login）登录，浏览器自动跳转 |
| API Key | 程序调用 / 脚本 | 请求头 `X-API-Key` 或参数 `?apikey=xxx` |

### API Key

首次启动自动生成，格式 `axp-{32位hex}`，存储在 `.env` 的 `API_KEY` 字段。

```bash
# 查看当前 API Key
cat .env | grep API_KEY

# 使用 API Key 调用
curl -H "X-API-Key: axp-xxxxxxxx" http://localhost:5557/convert -F "files=@test.dwg"

# 或通过 URL 参数
curl "http://localhost:5557/convert?apikey=axp-xxxxxxxx" -F "files=@test.dwg"
```

### SSO 登录流程

1. 浏览器访问 `http://host:5557/` → 自动跳转到统一登录平台
2. 输入用户名密码登录 → 自动跳回 `/callback?ticket=xxx`
3. 后端验证 ticket，通过后存入 session，跳转至首页

---

## 接口列表

### DWG→PDF 转换

#### 上传转换

```
POST /convert
Content-Type: multipart/form-data
认证：SSO session 或 API Key
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| files | file[] | 是 | DWG 文件，支持多个同时上传 |
| merge | string | 否 | 合并相邻图框，`"true"` / `"false"`，默认 `"false"` |
| workers | int | 否 | 并发线程数，默认使用配置值 |

**响应（立即返回）**

```json
{
  "task_id": "a1b2c3d4e5f6",
  "status": "running",
  "total": 3,
  "workers": 6
}
```

**示例**

```bash
# 单文件
curl -X POST http://localhost:5557/convert \
  -H "X-API-Key: axp-xxxxxxxx" \
  -F "files=@图纸1.dwg"

# 多文件 + 合并图框
curl -X POST http://localhost:5557/convert \
  -H "X-API-Key: axp-xxxxxxxx" \
  -F "files=@图纸1.dwg" \
  -F "files=@图纸2.dwg" \
  -F "merge=true"
```

#### 查询任务状态

```
GET /task/<task_id>
```

```json
{
  "id": "a1b2c3d4e5f6",
  "status": "done",
  "total": 3,
  "results": [
    {
      "file": "图纸1.dwg",
      "success": true,
      "pdf_count": 2,
      "elapsed": 45.2,
      "borders": [
        {"name": "TK", "size_label": "A1", "width_mm": 841, "height_mm": 594}
      ]
    }
  ],
  "ok_count": 3,
  "total_pdfs": 5,
  "total_time": 52.1,
  "zip_size_kb": 1024.5
}
```

#### 列出所有任务

```
GET /tasks
```

#### 下载结果 ZIP

```
GET /download/<task_id>
```

ZIP 包内容（按 DWG 分组）：

```
result.zip
├── 图纸1.zip          ← 每个 DWG 单独打包
│   ├── 图纸1.dwg      ← 原始 DWG
│   ├── 图纸1.dxf      ← DXF 中间文件
│   ├── 01-图纸1-A1.pdf
│   └── 02-图纸1-A2.pdf
└── 图纸2.zip
    ├── 图纸2.dwg
    └── 01-图纸2-A1.pdf
```

---

### PDF→DWG 转换

#### 批量转换（调度入口）

```
POST /convert-pdf
Content-Type: multipart/form-data
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

#### 单文件转换（Worker 端点）

```
POST /convert-pdf-single
Content-Type: multipart/form-data
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | file | 是 | 单个 PDF 文件 |
| acad_exe | string | 否 | AutoCAD acad.exe 路径 |
| timeout | int | 否 | 超时秒数，默认 300 |

#### 追加文件到运行中的任务

```
POST /convert-pdf/add/<task_id>
Content-Type: multipart/form-data
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| files | file[] | 是 | 要追加的 PDF 文件 |

#### PDF 任务状态

```
GET /pdf-task/<task_id>
```

#### 列出 PDF 任务

```
GET /pdf-tasks
```

#### 下载 PDF→DWG 结果

```
GET /download-pdf-zip/<task_id>     # 全部 DWG 的 ZIP
GET /download-pdf/<task_id>/<filename>  # 单个 DWG 文件
```

---

### 实时进度（SSE）

```
GET /stream
```

Server-Sent Events 流，事件类型：

| 事件 | 说明 |
|------|------|
| `connected` | 连接建立 |
| `heartbeat` | 心跳（30 秒间隔） |
| `task_start` | DWG 任务开始 |
| `file_start` | 单文件开始转换 |
| `file_done` | 单文件转换完成 |
| `task_done` | DWG 任务全部完成 |
| `pdf_task_start` | PDF 任务开始 |
| `pdf_file_done` | 单个 PDF 转换完成 |
| `pdf_task_done` | PDF 任务全部完成 |
| `pdf_task_add` | PDF 任务追加文件 |
| `worker_status` | Worker 节点状态更新 |

**示例**

```javascript
const es = new EventSource('/stream');
es.addEventListener('file_done', (e) => {
  const data = JSON.parse(e.data);
  console.log(data.file, data.success, data.pdf_count);
});
```

---

### 配置管理

#### 获取运行配置

```
GET /config
```

所有登录用户可访问。

```json
{
  "printer": "DWG To PDF.pc3",
  "plot_style": "monochrome.ctb",
  "timeout": 180,
  "border_keywords": "TK,TUKUANG,BORDER,FRAME,TITLE",
  "merge_borders": false,
  "auto_paper_size": true,
  "split_borders": true,
  "max_workers": 6,
  "t3_mode": true
}
```

#### 修改运行配置

```
POST /config
Content-Type: application/json
认证：需 SSO 登录
```

所有登录用户可修改打印参数。

```bash
curl -X POST http://localhost:5557/config \
  -b "session_cookie" \
  -H "Content-Type: application/json" \
  -d '{"max_workers": 8, "timeout": 300}'
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
| t3_mode | bool | 天正 T3 模式 |

#### 系统管理配置（仅管理员）

```
GET  /admin/config    # 查看系统配置
POST /admin/config    # 修改系统配置（写入 .env）
认证：需 SSO 登录且 is_admin=1
```

| 字段 | 说明 |
|------|------|
| api_key | API 密钥（只读，Web 端点击复制） |
| acad_path | accoreconsole.exe 路径 |
| acad_exe | acad.exe 路径 |
| tarch_arx | 天正 ARX 插件路径 |
| acad_template | 模板 DWG 路径 |
| work_dir | 工作目录 |

---

### 其他接口

| 接口 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/` | GET | SSO | Web UI 首页 |
| `/health` | GET | 无 | 健康检查 `{"status":"ok","workers":6}` |
| `/auth/check` | GET | 无 | 检查登录状态 |
| `/logout` | GET | 无 | 退出登录 |
| `/callback` | GET | 无 | SSO ticket 回调（自动处理） |
| `/logs?lines=200` | GET | 无 | 获取最近日志 |

---

## SSE 事件数据格式

### DWG→PDF 事件

**task_start**
```json
{"task_id": "xxx", "total": 3, "workers": 6}
```

**file_done**
```json
{
  "task_id": "xxx", "file": "图纸1.dwg",
  "success": true, "pdf_count": 2,
  "elapsed": 45.2, "error": null
}
```

**task_done**
```json
{
  "task_id": "xxx", "total_time": 52.1,
  "ok_count": 3, "total": 3, "total_pdfs": 5,
  "workers": 6, "zip_size_kb": 1024.5
}
```

### PDF→DWG 事件

**pdf_task_start**
```json
{"task_id": "xxx", "total": 5}
```

**pdf_file_done**
```json
{
  "task_id": "xxx", "file": "test.pdf",
  "ok": true, "elapsed": 30.5, "worker": "local"
}
```

**pdf_task_done**
```json
{
  "task_id": "xxx", "total_time": 120.3,
  "ok_count": 5, "total": 5, "zip_size_kb": 2048.0,
  "workers_status": {"local": {"done": 5, "avg_time": 24.1, "active": 0}}
}
```

---

## 多机调度（PDF→DWG）

通过 `workers.json` 配置多台 Worker 节点，使用 SWRR（平滑加权轮询）算法自动分配任务。

```json
{
  "workers": [
    {"name": "local", "url": "http://localhost:5557", "max_slots": 4},
    {"name": "server-b", "url": "http://192.168.1.100:5557", "max_slots": 4}
  ],
  "acad_exe": "C:\\opt\\AutoCAD 2026\\acad.exe",
  "timeout": 300
}
```

- 每个 Worker 最多 `max_slots` 个并发任务
- 转换速度快的 Worker 自动获得更高权重，分配更多任务
- 同机多实例只需配置不同端口

---

## 配置文件

### .env

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| ACAD_PATH | `C:\opt\AutoCAD 2026\accoreconsole.exe` | accoreconsole 路径（DWG→PDF） |
| ACAD_EXE | `C:\opt\AutoCAD 2026\acad.exe` | acad.exe 路径（PDF→DWG、非 T3 模式） |
| TARCH_ARX | `C:\opt\T30-PlugInV1.0\sys25x64\tch_kernal.arx` | 天正 ARX 插件路径 |
| ACAD_TEMPLATE | `C:\opt\ACADxPDF\Template\mt.dwg` | 模板 DWG（加载 ARX 用） |
| WORK_DIR | 空（使用 `_work/`） | 工作目录 |
| PRINTER | `DWG To PDF.pc3` | 打印机配置 |
| PLOT_STYLE | `monochrome.ctb` | 打印样式 |
| TIMEOUT | `180` | 单文件超时（秒） |
| BORDER_KEYWORDS | `TK,TUKUANG,BORDER,FRAME,TITLE` | 图框块名关键字 |
| MAX_WORKERS | `6` | 最大线程数 |
| API_HOST | `0.0.0.0` | 监听地址 |
| API_PORT | `5557` | 监听端口 |
| API_KEY | 自动生成 | API 密钥 |
| SSO_URL | `http://192.168.0.8:80` | SSO 登录平台地址 |

### workers.json

PDF→DWG 多机调度配置，详见上方「多机调度」章节。

---

## 权限说明

| 角色 | 权限 |
|------|------|
| 未登录 | 仅可访问 `/health`、`/stream` |
| SSO 登录用户 | 使用 Web UI、修改打印参数、查看配置 |
| 管理员 (`is_admin=1`) | 额外可修改系统配置（API Key、CAD 路径等） |
| API Key | 可调用所有转换接口（等同已登录用户） |

---

## 图框检测

1. **块名匹配**（优先）— 查找 INSERT 块名称含 `BORDER_KEYWORDS` 中关键字的块参照，匹配短边 ≥280mm 的标准纸幅
2. **矩形检测**（兜底）— 扫描封闭 LWPOLYLINE 矩形和 LINE 围合矩形，过滤匹配标准纸幅，去除包含关系

检测到的每个图框独立输出一张 PDF，命名规则：`{编号}-{DWG文件名}-{纸幅}.pdf`

## 天正（TArch）支持

- **T3 模式**（默认开启）：使用 accoreconsole 直接处理，天正实体已导出为标准 AutoCAD 实体
- **非 T3 模式**：自动切换到 acad.exe，通过 `/ld` 参数加载天正 ARX 插件解析代理实体，使用模板 DWG 启动避免弹窗

## 错误码

| HTTP 状态码 | 说明 |
|-------------|------|
| 400 | 请求参数错误（无文件、格式不对） |
| 401 | 未认证（未登录且无 API Key） |
| 403 | 权限不足（非管理员访问管理接口） |
| 404 | 任务不存在或结果已过期 |

## 注意事项

- 任务完成后保留 1 小时，超时自动清理（含上传文件和输出结果）
- 中文文件名完全支持（内部自动转换为 ASCII 安全名称处理）
- 日志输出到 `logs/api.log`，按 20MB 轮转，保留 5 份
