# ACADxPDF

AutoCAD DWG↔PDF 双向批量转换工具，支持图框自动识别与分页输出。运行在 Windows 环境。

## 功能

- **DWG→PDF** — 自动检测图框，每个图框独立输出一张 PDF，智能匹配纸张
- **PDF→DWG** — 通过 AutoCAD PDFIMPORT 反向转换，批量处理
- **图框自动识别** — 块名匹配检测（优先）+ 封闭矩形检测（兜底）
- **纸张自适应** — 根据图框尺寸自动匹配标准纸幅（A0–A4），支持加长幅面
- **多线程并行** — 本地多 Worker 线程并发转换
- **多机分布式** — 拉取式 Worker 架构，支持远程机器接入
- **Web UI** — 浏览器上传文件，SSE 实时进度，在线配置参数，下载 ZIP
- **REST API** — API Key 认证，供其他程序调用
- **SSO 登录** — Web UI 走统一登录，API 走 API Key

## 环境要求

- Windows Server / Windows 10+
- AutoCAD 2026（需 `acad.exe`，天正需 `tch_kernal.arx`）
- Python 3.10+

## 安装

```bash
pip install ezdxf flask requests
```

## 使用

### 启动服务

```bash
python run.py
# 浏览器打开 http://localhost:5557
# API Key 首次启动自动生成，查看 .env 中的 API_KEY
```

### API 调用

**DWG→PDF：**

```bash
# 上传 DWG
curl -X POST http://localhost:5557/convert \
  -H "X-API-Key: axp-xxxxxxxx" \
  -F "files=@图纸1.dwg" \
  -F "files=@图纸2.dwg"

# 查询任务状态
curl -H "X-API-Key: axp-xxxxxxxx" http://localhost:5557/task/<task_id>

# 下载结果 ZIP
curl -H "X-API-Key: axp-xxxxxxxx" -O http://localhost:5557/download/<task_id>
```

**PDF→DWG：**

```bash
# 上传 PDF
curl -X POST http://localhost:5557/convert-pdf \
  -H "X-API-Key: axp-xxxxxxxx" \
  -F "files=@test1.pdf" \
  -F "files=@test2.pdf"

# 下载结果
curl -H "X-API-Key: axp-xxxxxxxx" -O http://localhost:5557/download-pdf-zip/<task_id>
```

**实时进度（SSE）：**

```bash
curl http://localhost:5557/stream
```

### 命令行 — 单文件转换

```python
from acad2pdf.converter import convert_dwg_lsp

result = convert_dwg_lsp("C:/path/to/drawing.dwg", "C:/path/to/output")
```

### 远程 Worker

在另一台装有 AutoCAD 的机器上：

```bash
# 创建 worker.json
{
  "master_url": "http://192.168.0.5:5557",
  "worker_id": "node-A",
  "capacity": 4,
  "api_key": "axp-xxxxxxxx",
  "acad_exe": "C:\\opt\\AutoCAD 2026\\acad.exe",
  "timeout": 300
}

# 启动
python -m acad2pdf.worker
```

## 输出命名规则

DWG→PDF 每个 PDF 以 `{文件名}_{序号}_{纸幅}.pdf` 命名。

结果 ZIP 包按原始文件名分组：

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

## 项目结构

```
ACADxPDF/
├── acad2pdf/                # 核心包
│   ├── converter.py         # DWG→PDF 转换核心（图框识别、LSP 脚本生成）
│   ├── api.py               # Flask API + Web UI + SSE + 配置管理
│   ├── worker.py            # Worker 线程（拉取式，支持本地/远程）
│   ├── task_store.py        # 统一任务模型 + 内存队列 + Worker 注册表
│   ├── dispatch_routes.py   # 调度端点（pull/result/register/heartbeat）
│   ├── pdf2dwg_api.py       # PDF→DWG Blueprint
│   ├── pdf2dwg_worker.py    # PDF→DWG 单文件转换（PDFIMPORT）
│   ├── static/              # Web 前端
│   └── plot_styles/         # CTB 打印样式文件
├── lsp/                     # AutoLISP 模块
│   ├── autoplot.lsp          # 主入口
│   ├── ap-detect.lsp         # 图框检测
│   ├── ap-plot.lsp           # 打印输出
│   ├── ap-paper.lsp          # 纸张匹配
│   └── ap-batch.lsp          # 批量处理
├── Template/                # 模板 DWG
├── run.py                   # 启动入口（注册 Blueprint + 启动本地 Worker）
├── docs/                    # 文档
├── _work/                   # 运行时临时目录（自动清理）
└── .env                     # 运行时配置
```

## 配置

编辑 `.env`：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ACAD_PATH` | `C:\Autodesk\AutoCAD 2020\accoreconsole.exe` | accoreconsole 路径 |
| `ACAD_EXE` | `C:\opt\AutoCAD 2026\acad.exe` | acad.exe 路径 |
| `TARCH_ARX` | `C:\opt\T30-PlugInV1.0\sys25x64\tch_kernal.arx` | 天正 ARX 插件 |
| `ACAD_TEMPLATE` | `C:\opt\ACADxPDF\Template\mt.dwg` | 模板 DWG |
| `WORK_DIR` | (空→`_work/`) | 工作目录 |
| `PRINTER` | `DWG To PDF.pc3` | 打印机配置 |
| `PLOT_STYLE` | `monochrome.ctb` | 打印样式表 |
| `TIMEOUT` | `180` | 单文件超时（秒） |
| `BORDER_KEYWORDS` | `TK,TUKUANG,BORDER,FRAME,TITLE` | 图框块名关键词 |
| `MAX_WORKERS` | `6` | Worker 线程数 |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `5557` | 服务监听地址 |
| `API_KEY` | 自动生成 | API 密钥 |
| `SSO_URL` | `http://192.168.0.8:80` | SSO 登录地址 |

## 架构

### 统一调度

```
┌──────────┐  pull    ┌──────────┐  convert  ┌──────────┐
│  主 API   │ ◄────── │  Worker   │ ──────── │ AutoCAD  │
│ (Flask)  │ ──────► │ (线程/远程)│  result   │ acad.exe │
│          │  result  │          │           │          │
└──────────┘          └──────────┘           └──────────┘
     ▲
     │ SSE / HTTP
     ▼
┌──────────┐
│  Web UI  │
└──────────┘
```

- **主 API**：接收上传、管理任务队列、提供下载、广播 SSE 事件
- **本地 Worker**：主进程内的后台线程，直接注册到内存队列
- **远程 Worker**：独立进程，通过 HTTP `/dispatch/*` 端点拉取任务、回传结果
- **统一任务模型**：DWG→PDF 和 PDF→DWG 共用同一个 TaskStore

### Worker 生命周期

1. 注册 → `POST /dispatch/register`
2. 拉取任务 → `POST /dispatch/pull`
3. 下载源文件 → `GET /dispatch/file/<id>`
4. 转换 → 调用 AutoCAD
5. 上传结果 → `POST /dispatch/result`（multipart，含 PDF/DWG/DXF）
6. 心跳 → `POST /dispatch/heartbeat`（30 秒间隔，90 秒超时回收）

## 认证

| 方式 | 适用场景 | 说明 |
|------|----------|------|
| SSO 登录 | Web 浏览器 | 通过统一登录平台登录，自动跳转 |
| API Key | 程序调用 | 请求头 `X-API-Key` 或参数 `?apikey=xxx` |

## License

MIT License. See [LICENSE](LICENSE).
