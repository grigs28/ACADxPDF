# DWG 导出 API 规格（ACAD_TABLE 方案）

## 概述

在 Windows（192.168.0.5）上部署一个 API 服务，接收评分数据，通过 pyautocad（COM 自动化）调用 AutoCAD 生成 ACAD_TABLE 实体的 DWG 文件。

**架构：**

```
Linux Web(192.168.0.8)  ──HTTP POST──▶  Windows API(192.168.0.5)
                                          │
                                          ▼
                                      AutoCAD (COM)
                                          │
                                          ▼
                                      生成 DWG 文件
                                          │
                                          ▼
Linux Web  ◀──返回 DWG 文件流────────  返回文件
```

## 环境要求

| 项目 | 要求 |
|------|------|
| OS | Windows (192.168.0.5) |
| Python | 3.9+ |
| AutoCAD | 已安装（2018+），注册了 COM 组件 |
| pyautocad | `pip install pyautocad` |
| comtypes | `pip install comtypes`（pyautocad 依赖） |
| Flask | `pip install flask`（或 FastAPI） |

## API 接口

### `POST /api/export-dwg`

**请求：** JSON 格式的评分数据

```json
{
  "project_name": "xxx项目",
  "project_code": "2025-001",
  "target_star": 2,
  "standard_id": "gbt50378-2019",
  "template_dwg": "C:/opt/templates/gb50378_A1.dwg",
  "output_path": "C:/opt/output/{project_code}_评分表.dwg",
  "tables": [
    {
      "title": "4 安全耐久",
      "chapter": 4,
      "max_score": 100,
      "insert_point": [50, 800],
      "col_widths": [20, 80, 60, 30, 15, 15],
      "header_height": 8,
      "row_height": 12,
      "rows": [
        {
          "type": "header",
          "cells": ["序号", "评价条文", "技术措施", "得分", "等级", "满分"]
        },
        {
          "type": "control",
          "clause_id": "4.1.1",
          "cells": ["1", "4.1.1 场地安全...", "技术措施内容", "", "达标", "—"]
        },
        {
          "type": "score",
          "clause_id": "4.2.1",
          "cells": ["2", "4.2.1 采用...", "技术措施内容\n含表格", "10", "D3", "10"]
        },
        {
          "type": "subtotal",
          "cells": ["", "4 小计", "", "85", "", "100"]
        }
      ]
    },
    {
      "title": "5 健康舒适",
      "chapter": 5,
      "...": "..."
    }
  ]
}
```

**响应：** DWG 文件流（`application/octet-stream`）

```
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="xxx_评分表.dwg"
```

**错误响应：**

```json
{ "error": "AutoCAD not running" }
{ "error": "Template not found: ..." }
```

---

## 数据结构说明

### 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `project_name` | string | 项目名称，写入图框标题栏 |
| `project_code` | string | 项目编号 |
| `target_star` | int | 目标星级（0-3） |
| `standard_id` | string | 标准编号 |
| `template_dwg` | string | 模板 DWG 绝对路径（含图框、标题栏） |
| `output_path` | string | 输出 DWG 绝对路径 |
| `tables` | array | 各章节评分表（每个章节一个 ACAD_TABLE） |

### tables[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 章节标题（如"4 安全耐久"） |
| `chapter` | int | 章节号 |
| `max_score` | int | 章节满分 |
| `insert_point` | [x, y] | ACAD_TABLE 插入点坐标（图纸空间 mm） |
| `col_widths` | [float] | 各列宽度（mm） |
| `header_height` | float | 表头行高 |
| `row_height` | float | 数据行高 |
| `rows` | array | 表格行数据 |

### rows[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | `header`=表头, `control`=控制项, `score`=评分项, `subtotal`=小计 |
| `clause_id` | string | 条文编号（可选，小计行为空） |
| `cells` | [string] | 各列文本内容 |

**cells 内的换行**：用 `\n` 表示，对应 AutoCAD 单元格内的多行文本。
**cells 内的表格**：技术措施中的表格，用 `\n` + 制表符 `\t` 分隔列，API 端负责解析后在单元格内用多行文本模拟。

---

## AutoCAD COM 操作流程

```python
import win32com.client
from pyautocad import Autocad, APoint

def generate_dwg(data):
    # 1. 打开模板
    acad = Autocad()
    doc = acad.app.Documents.Open(data["template_dwg"])
    msp = doc.ModelSpace

    for table_data in data["tables"]:
        # 2. 创建 ACAD_TABLE
        n_rows = len(table_data["rows"])
        n_cols = len(table_data["col_widths"])
        insert = APoint(*table_data["insert_point"])

        table = msp.AddTable(
            insert,
            n_rows,
            n_cols,
            table_data["row_height"],
            table_data["col_widths"][0]  # 默认列宽，后面逐列设置
        )

        # 3. 设置列宽
        for col_idx, width in enumerate(table_data["col_widths"]):
            table.SetColumnWidth(col_idx, width)

        # 4. 设置表头行高
        table.SetRowHeight(0, table_data["header_height"])

        # 5. 填充数据
        for row_idx, row_data in enumerate(table_data["rows"]):
            table.SetRowHeight(row_idx, table_data["row_height"])

            for col_idx, cell_text in enumerate(row_data["cells"]):
                # 处理换行
                text = cell_text.replace("\\n", "\n")
                table.SetText(row_idx, col_idx, text)

                # 表头加粗
                if row_data["type"] == "header":
                    table.SetCellTextHeight(row_idx, col_idx, 3.5)
                    # table.SetCellTextStyle(row_idx, col_idx, "Standard")

            # 小计行特殊样式
            if row_data["type"] == "subtotal":
                for col_idx in range(n_cols):
                    table.SetCellBackgroundColor(
                        row_idx, col_idx,
                        win32com.client.Dispatch("AutoCAD.AcDbTable").acRow
                    )

        # 6. 设置表格样式（边框、字体等）
        table.SetTextHeight(1, 3.0)       # 数据行文字高度
        table.SetAlignment(1, 4)           # acMiddleCenter = 4

    # 7. 保存
    doc.SaveAs(data["output_path"])
    return data["output_path"]
```

---

## Flask 服务完整骨架

```python
# server.py — 运行在 192.168.0.5 (Windows)
import os
import time
import tempfile
from flask import Flask, request, send_file, jsonify
from dwg_generator import generate_dwg  # 上面 COM 操作封装在此

app = Flask(__name__)

@app.route("/api/export-dwg", methods=["POST"])
def export_dwg():
    data = request.get_json()
    if not data or "tables" not in data:
        return jsonify({"error": "Missing tables data"}), 400

    try:
        output_path = generate_dwg(data)
        return send_file(
            output_path,
            as_attachment=True,
            download_name=os.path.basename(output_path),
            mimetype="application/octet-stream"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8901)
```

---

## 调用端（Linux Web App）

在 `ai-optimizer` 中新增 API：

```python
# app/api/export_dwg.py
import requests
from fastapi import APIRouter

router = APIRouter(prefix="/api/export-dwg")

DWG_API = "http://192.168.0.5:8901/api/export-dwg"

@router.post("/{project_id}")
async def export_project_dwg(project_id: str):
    # 1. 从数据库查评分数据
    scoring_data = build_scoring_data(project_id)

    # 2. 调用 Windows API
    resp = requests.post(DWG_API, json=scoring_data, timeout=120)

    # 3. 返回 DWG 文件
    return StreamingResponse(
        resp.iter_content(chunk_size=8192),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={project_id}_评分表.dwg"}
    )
```

---

## 模板 DWG 要求

模板文件（`gb50378_A1.dwg`）应包含：
- **A1 图框**（594×841mm）
- **标题栏**（项目名称、编号、日期等占位文本）
- **图层设置**：`评分表`、`图框`、`文字` 等
- **文字样式**：`Standard`（宋体/TSSDNorm），`标题`（黑体）
- **不包含**评分表内容（API 动态创建 ACAD_TABLE）

各章节表的 `insert_point` 坐标需根据模板中图框位置确定。

---

## 技术措施中的表格处理

skill 字段存储 HTML（TinyMCE 编辑器输出），导出时需解析：

```python
from bs4 import BeautifulSoup

def html_table_to_acad_cell(html_content):
    """将 HTML 表格转为 AutoCAD 单元格内的多行文本"""
    soup = BeautifulSoup(html_content, "html.parser")
    tables = soup.find_all("table")

    if not tables:
        return html_content  # 无表格，原样返回

    lines = []
    for table in tables:
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            lines.append("  ".join(cells))  # 空格分隔列

    return "\n".join(lines)
```

**备选方案**：如果表格复杂（合并单元格等），可将 HTML 表格单独生成一个小 ACAD_TABLE，嵌入到主表单元格中。

---

## 注意事项

1. **AutoCAD 必须先启动**：pyautocad 通过 COM 控制，需要 AutoCAD 进程运行（可以最小化）
2. **并发问题**：COM 是单线程的，建议用队列（如 `queue.Queue`）串行处理请求
3. **超时**：大表格生成可能需要 30-60 秒，Linux 端调用 timeout 设 120 秒
4. **文件清理**：输出 DWG 下载后定期清理临时文件
5. **编码**：确保 JSON 中中文使用 UTF-8
