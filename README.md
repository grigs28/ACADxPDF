# ACADxPDF

AutoCAD DWG 批量转 PDF 工具，支持图框自动识别与分页输出。

## 功能

- **图框自动识别** — 两种检测策略：块名匹配检测（优先）+ 封闭矩形检测（兜底）
- **智能分页** — 每个图框独立输出一张 PDF，使用 Window 模式精确裁剪
- **纸张自适应** — 根据图框尺寸自动匹配标准纸幅（A0–A4），支持加长幅面（如 A1+0.5、A1+1）
- **批量处理** — 支持目录批量转换，输出统计（总时间、PDF 数量、平均耗时）
- **Flask API** — HTTP 接口上传 DWG 文件，返回 ZIP 包（含 DWG + DXF + PDF）
- **可选合并** — 相邻图框可合并输出为单张 PDF

## 环境要求

- Windows 10+
- AutoCAD 2020（需安装 `accoreconsole.exe`）
- Python 3.10+
- conda 环境：`pdf`

## 安装

```bash
conda activate pdf
pip install ezdxf flask
```

## 使用

### 命令行 — 单文件转换

```python
from acad2pdf import convert_dwg

result = convert_dwg(
    r"C:\path\to\drawing.dwg",
    output_dir=r"C:\path\to\output",
    split_borders=True,
    auto_paper_size=True,
)
```

### 命令行 — 批量转换

```python
from acad2pdf import batch_convert

results = batch_convert(
    input_dir=r"C:\path\to\dwg_folder",
    output_dir=r"C:\path\to\output",
    split_borders=True,
    auto_paper_size=True,
)
```

### API 服务

```bash
python api.py
# 服务启动在 http://0.0.0.0:5000
```

**上传转换：**

```bash
curl -X POST http://localhost:5000/convert \
  -F "files=@drawing1.dwg" \
  -F "files=@drawing2.dwg" \
  -F "merge=false" \
  --output result.zip
```

返回的 ZIP 包含：原始 DWG、DXF 中间文件、分页 PDF。

**健康检查：**

```bash
curl http://localhost:5000/health
```

## 输出命名规则

```
{编号}-{DWG文件名}-{纸幅}.pdf
```

示例：`01-n-A1.pdf`、`02-n-A1+0.5.pdf`、`03-建筑设计说明-A2.pdf`

## 项目结构

```
ACADxPDF/
├── acad2pdf.py        # 核心转换模块（图框识别、PDF 生成、批量处理）
├── api.py             # Flask API 服务
├── analyze_dxf.py     # DXF 分析工具
├── test_api.py        # API 测试脚本
└── .gitignore
```

## 配置

复制 `.env.example` 为 `.env`，按需修改：

```ini
ACAD_PATH=C:\Autodesk\AutoCAD 2020\accoreconsole.exe
ACAD_UNIT=毫米
PRINTER=DWG To PDF.pc3
PLOT_STYLE=monochrome.ctb
TIMEOUT=180
BORDER_KEYWORDS=TK,TUKUANG,BORDER,FRAME,TITLE
API_HOST=0.0.0.0
API_PORT=5000
```

## 图框识别原理

1. **块名匹配**（优先）— 查找名称含 `BORDER_KEYWORDS` 中关键字的 INSERT 块，计算其包围盒作为图框边界
2. **矩形检测**（兜底）— 扫描模型空间和块定义中的封闭 LWPOLYLINE 矩形，筛选短边匹配标准纸幅的矩形，去除内层包含

## License

MIT License. See [LICENSE](LICENSE).

Copyright (c) 2025 grigs28
