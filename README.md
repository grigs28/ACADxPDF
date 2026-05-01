# ACADxPDF

AutoCAD DWG 批量转 PDF 工具，支持图框自动识别与分页输出。运行在 WSL2 环境。

## 功能

- **图框自动识别** — 两种检测策略：块名匹配检测（优先）+ 封闭矩形检测（兜底）
- **智能分页** — 每个图框独立输出一张 PDF，使用 Window 模式精确裁剪
- **纸张自适应** — 根据图框尺寸自动匹配标准纸幅（A0–A4），支持加长幅面（如 A1+0.5、A1+1）
- **批量处理** — 支持目录批量转换，输出统计（总时间、PDF 数量、平均耗时）
- **多线程并行** — ThreadPoolExecutor 队列消费，W=6 时可达 4.8x 加速
- **Web UI** — 浏览器上传 DWG，SSE 实时进度，在线配置参数，下载 ZIP
- **Flask API** — HTTP 接口上传 DWG 文件，返回 ZIP 包
- **可选合并** — 相邻图框可合并输出为单张 PDF

## 环境要求

- WSL2 (Windows Subsystem for Linux)
- AutoCAD 2022（需安装 `accoreconsole.exe`）
- Python 3.10+
- conda 环境：`pdf`

## 安装

```bash
conda activate pdf
pip install ezdxf flask
```

## 使用

### Web UI

```bash
conda activate pdf
python -m acad2pdf.api
# 浏览器打开 http://localhost:5557
```

### 命令行 — 单文件转换

```python
from acad2pdf import convert_dwg

result = convert_dwg(
    "/mnt/c/path/to/drawing.dwg",
    output_dir="/mnt/c/path/to/output",
    split_borders=True,
    auto_paper_size=True,
)
```

### 命令行 — 批量转换

```python
from acad2pdf import batch_convert

results = batch_convert(
    input_dir="/mnt/c/path/to/dwg_folder",
    output_dir="/mnt/c/path/to/output",
    split_borders=True,
    auto_paper_size=True,
)
```

### API 服务

```bash
python -m acad2pdf.api
# 服务启动在 http://0.0.0.0:5557
```

**上传转换：**

```bash
curl -X POST http://localhost:5557/convert \
  -F "files=@drawing1.dwg" \
  -F "files=@drawing2.dwg" \
  -F "merge=false" \
  --output result.zip
```

**实时进度（SSE）：**

```bash
curl http://localhost:5557/stream
```

**健康检查：**

```bash
curl http://localhost:5557/health
```

**查看/修改配置：**

```bash
curl http://localhost:5557/config
curl -X POST http://localhost:5557/config -d '{"max_workers": 6}' -H 'Content-Type: application/json'
```

## 输出命名规则

```
{编号}-{DWG文件名}-{纸幅}.pdf
```

示例：`01-建筑设计说明-A1.pdf`、`02-建筑设计说明-A1+0.5.pdf`、`03-暖通说明-A2.pdf`

## 项目结构

```
ACADxPDF/
├── acad2pdf/              # 核心包
│   ├── __init__.py        # 公共接口导出
│   ├── converter.py       # 转换核心（图框识别、PDF 生成、批量处理）
│   ├── api.py             # Flask API + Web UI + SSE
│   └── static/            # Web 前端
│       └── index.html
├── tools/                 # 工具脚本
│   ├── analyze_dxf.py     # DXF 分析工具
│   ├── bench_threads.py   # 多线程基准测试
│   └── test_api.py        # API 测试脚本
├── docs/                  # 文档
│   ├── 多线程基准测试报告.md
│   ├── 线程性能测试报告.md
│   └── API说明文档.md
├── _work/                 # 运行时临时目录（每个 DWG 独立子目录）
├── .env                   # 运行时配置
├── .gitignore
├── README.md
└── LICENSE
```

## 配置

编辑 `.env`，按需修改：

```ini
ACAD_PATH=/mnt/c/opt/AutoCAD 2022/accoreconsole.exe
WORK_DIR=
ACAD_UNIT=毫米
PRINTER=DWG To PDF.pc3
PLOT_STYLE=monochrome.ctb
TIMEOUT=180
BORDER_KEYWORDS=TK,TUKUANG,BORDER,FRAME,TITLE
API_HOST=0.0.0.0
API_PORT=5557
MAX_WORKERS=6
LOG_MAX_BYTES=20971520
LOG_BACKUP_COUNT=5
```

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ACAD_PATH` | `/mnt/c/opt/AutoCAD 2022/accoreconsole.exe` | accoreconsole 路径（Windows 文件系统） |
| `WORK_DIR` | (空) | 工作目录，留空则使用项目下 `_work/` |
| `PRINTER` | `DWG To PDF.pc3` | PC3 打印机配置 |
| `PLOT_STYLE` | `monochrome.ctb` | 打印样式表 |
| `TIMEOUT` | `180` | 单文件超时（秒） |
| `BORDER_KEYWORDS` | `TK,TUKUANG,BORDER,FRAME,TITLE` | 图框块名关键词 |
| `MAX_WORKERS` | `6` | 线程池大小（推荐 4-8） |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `5557` | 服务监听地址 |

## 多线程性能

基于 32 个 DWG（196 PDF）的基准测试：

| 线程数 | 总耗时 | 加速比 | 内存 |
|--------|--------|--------|------|
| 1 | 391.7s | 1.00x | 65MB |
| 4 | 113.2s | 3.46x | 98MB |
| 6 | 81.0s | 4.84x | 120MB |
| 8 | 69.9s | 5.60x | 131MB |
| 12 | 56.9s | 6.88x | 176MB |

详见 [线程性能测试报告](docs/线程性能测试报告.md)。

## 架构

```
1 DWG = 1 个独立工作目录 + 1 次 accoreconsole(DXF) + 1 次 accoreconsole(全部PDF)
```

- `_work/xxxxxxxx/` — 每个 DWG 的独立工作目录（DWG 副本、DXF、临时 PDF）
- 所有 -PLOT 命令合并为一个 AutoLISP 脚本，单次 accoreconsole 执行
- 最终 PDF 从 `_work/` 移到 `output/`，临时目录自动清理

## 图框识别原理

1. **块名匹配**（优先）— 查找名称含 `BORDER_KEYWORDS` 中关键字的 INSERT 块，计算其包围盒作为图框边界
2. **矩形检测**（兜底）— 扫描模型空间和块定义中的封闭 LWPOLYLINE 矩形，筛选短边匹配标准纸幅的矩形，去除内层包含

## License

MIT License. See [LICENSE](LICENSE).

Copyright (c) 2025 grigs28
