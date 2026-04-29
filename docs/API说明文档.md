# ACADxPDF API 说明文档

## 概述

ACADxPDF 提供 REST API 服务，支持上传 DWG 文件并自动转换为 PDF。

## 启动服务

```bash
conda activate pdf
python -m acad2pdf.api
```

服务默认监听 `0.0.0.0:5557`，可通过 `.env` 中的 `API_HOST` 和 `API_PORT` 修改。

## 接口列表

### 1. 上传转换

**请求**

```
POST /convert
Content-Type: multipart/form-data
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| files | file[] | 是 | DWG 文件，支持多个 |
| merge | string | 否 | 是否合并相邻图框，`"true"` 或 `"false"`，默认 `"false"` |

**示例**

```bash
# 单文件（每个图框一张PDF）
curl -X POST http://localhost:5557/convert \
  -F "files=@住宅设计说明.dwg" \
  --output result.zip

# 多文件，合并图框为一张PDF
curl -X POST http://localhost:5557/convert \
  -F "files=@图纸1.dwg" \
  -F "files=@图纸2.dwg" \
  -F "merge=true" \
  --output result.zip
```

**响应**

- 成功：返回 ZIP 文件（`Content-Type: application/zip`）
- 失败：返回 JSON 错误信息

| 状态码 | 说明 |
|--------|------|
| 200 | 成功，返回 ZIP |
| 400 | 未上传文件或无有效 DWG |

**ZIP 包内容**

| 文件 | 说明 |
|------|------|
| `*.dwg` | 原始 DWG 文件 |
| `*.dxf` | DXF 中间文件 |
| `*.pdf` | 转换后的 PDF（每个图框一张） |

PDF 命名规则：`{编号}-{DWG文件名}-{纸幅}.pdf`

示例：
```
01-住宅设计说明-A1.pdf
02-住宅设计说明-A1.pdf
01-公建设计说明-A2.pdf
```

### 2. 健康检查

**请求**

```
GET /health
```

**响应**

```json
{"status": "ok"}
```

## 日志

API 日志输出到 `logs/api.log`，同时输出到控制台。日志内容包括：

- 服务启动信息
- 请求处理（文件名、merge 参数）
- 转换结果（PDF 数量、耗时）
- ZIP 打包信息

## 图框识别流程

1. **块名匹配**（优先）— 查找名称含 `BORDER_KEYWORDS` 配置项中关键字的 INSERT 块
2. **矩形检测**（兜底）— 扫描封闭 LWPOLYLINE 矩形，匹配标准纸幅

## 配置项

通过 `.env` 文件配置，参考 `.env.example`：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| ACAD_PATH | `C:\Autodesk\AutoCAD 2020\accoreconsole.exe` | AutoCAD 路径 |
| ACAD_UNIT | `毫米` | 单位名称（中文 AutoCAD 用"毫米"） |
| PRINTER | `DWG To PDF.pc3` | 打印机名称 |
| PLOT_STYLE | `monochrome.ctb` | 打印样式 |
| TIMEOUT | `180` | 单文件超时（秒） |
| BORDER_KEYWORDS | `TK,TUKUANG,BORDER,FRAME,TITLE` | 图框块名关键字（逗号分隔） |
| API_HOST | `0.0.0.0` | API 监听地址 |
| API_PORT | `5557` | API 监听端口 |

## 错误处理

| 错误信息 | 原因 |
|----------|------|
| `no files uploaded` | 请求中未包含 files 字段 |
| `no valid DWG files` | 上传的文件中没有 .dwg 后缀的文件 |

## 注意事项

- 中文文件名完全支持（DWG、DXF、PDF 均可）
- 每次转换都会重新生成 DXF，不使用缓存
- 转换完成后自动清理所有临时文件
- 单文件默认超时 180 秒，可通过 `TIMEOUT` 配置调整
