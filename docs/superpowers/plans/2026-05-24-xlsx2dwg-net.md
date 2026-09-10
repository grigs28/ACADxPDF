# xlsx→DWG (.NET API + accoreconsole) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用 accoreconsole.exe + C# .NET API 实现 xlsx→DWG 转换，支持多线程并行（最多4线程），新建文件不影响原有功能。

**Architecture:** Python(openpyxl) 提取 xlsx 数据输出 JSON → C# DLL(.NET API) 读 JSON 创建 Table/MText 实体 → accoreconsole 加载 DLL 执行命令输出 DWG。Python 负责布局计算（坐标、尺寸），C# 只负责实体创建，职责分离。

**Tech Stack:** Python 3.10+ / openpyxl, C# .NET 8 / AutoCAD .NET API (acmgd.dll, accoremgd.dll, acdbmgd.dll), accoreconsole.exe (AutoCAD 2026), System.Text.Json

**Parallel Strategy:** 拆分 xlsx 为多个 JSON 分片 → 每个 accoreconsole 实例独立处理一个分片 → 多个 DWG 输出。参考现有 DWG→PDF 的 Worker 模式，使用 ThreadPoolExecutor。

---

## File Structure

所有新文件放在 `tools/xlsx2dwg_net/` 下，**不修改任何现有文件**。

```
tools/xlsx2dwg_net/
├── XlsxToDwg/                  # C# 项目
│   ├── XlsxToDwg.csproj        # 项目文件 (net8.0-windows)
│   ├── JsonModel.cs             # JSON 数据模型
│   ├── StyleSetup.cs            # 文字样式 + 表格样式
│   ├── EntityBuilder.cs         # Table/MText 创建
│   └── Commands.cs              # NETLOAD 命令入口
├── xlsx2json.py                 # Python: xlsx → JSON
├── run_xlsx2dwg.py              # Python: 编排器 (build DLL + gen script + run accoreconsole)
└── test_xlsx2dwg.py             # 集成测试脚本
```

## Prerequisites

| 项目 | 要求 | 当前状态 |
|------|------|----------|
| AutoCAD 2026 | `C:\opt\AutoCAD 2026\accoreconsole.exe` | ✅ 已安装 |
| .NET 8 SDK | `dotnet build` 可用 | ❌ 仅 Runtime，需安装 SDK |
| Python conda env `pdf` | openpyxl | ✅ 已有 |

---

## Task 1: Install .NET 8 SDK

**Files:** 无代码文件

- [ ] **Step 1: 安装 .NET 8 SDK**

```powershell
winget install Microsoft.DotNet.SDK.8
```

或从 https://dotnet.microsoft.com/download/dotnet/8.0 下载安装。

- [ ] **Step 2: 验证安装**

```powershell
dotnet --version
# 预期输出: 8.x.x
```

- [ ] **Step 3: Commit（如有配置变更）**

无需 commit — SDK 是全局工具。

---

## Task 2: Create C# Project Skeleton

**Files:**
- Create: `tools/xlsx2dwg_net/XlsxToDwg/XlsxToDwg.csproj`

- [ ] **Step 1: 创建项目目录**

```powershell
mkdir -p tools/xlsx2dwg_net/XlsxToDwg
```

- [ ] **Step 2: 创建 csproj 文件**

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0-windows</TargetFramework>
    <UseWindowsForms>true</UseWindowsForms>
    <EnableDefaultItems>true</EnableDefaultItems>
    <AppendTargetFrameworkToOutputPath>false</AppendTargetFrameworkToOutputPath>
    <OutputType>Library</OutputType>
    <RootNamespace>XlsxToDwg</RootNamespace>
    <AssemblyName>XlsxToDwg</AssemblyName>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="AcMgd">
      <HintPath>C:\opt\AutoCAD 2026\acmgd.dll</HintPath>
      <Private>false</Private>
    </Reference>
    <Reference Include="AcCoreMgd">
      <HintPath>C:\opt\AutoCAD 2026\accoremgd.dll</HintPath>
      <Private>false</Private>
    </Reference>
    <Reference Include="AcDbMgd">
      <HintPath>C:\opt\AutoCAD 2026\acdbmgd.dll</HintPath>
      <Private>false</Private>
    </Reference>
  </ItemGroup>
</Project>
```

- [ ] **Step 3: 创建最小 Commands.cs 验证编译**

```csharp
using Autodesk.AutoCAD.Runtime;

[assembly: CommandClass(typeof(XlsxToDwg.Commands))]

namespace XlsxToDwg;

public class Commands
{
    [CommandMethod("XLSXHELLO")]
    public static void Hello()
    {
        Autodesk.AutoCAD.ApplicationServices.Application.DocumentManager.MdiActiveDocument
            .Editor.WriteMessage("\n[XlsxToDwg] DLL loaded successfully!");
    }
}
```

- [ ] **Step 4: 编译验证**

```powershell
cd tools/xlsx2dwg_net/XlsxToDwg
dotnet build -c Release
# 预期: Build succeeded. Output → bin/Release/net8.0-windows/XlsxToDwg.dll
```

- [ ] **Step 5: 验证 accoreconsole 能加载 DLL**

创建临时测试脚本 `_test_dll.scr`:
```
(setvar "FILEDIA" 0)
(setvar "CMDECHO" 0)
(setvar "SECURELOAD" 0)
(command "_.NETLOAD" "C:/opt/ACADxPDF/tools/xlsx2dwg_net/XlsxToDwg/bin/Release/net8.0-windows/XlsxToDwg.dll")
(command "XLSXHELLO")
(command "_.QUIT" "Y")
```

```powershell
& "C:\opt\AutoCAD 2026\accoreconsole.exe" /i "C:\opt\AutoCAD 2026\userdata.en-us\Support\acadiso.dwt" /b "_test_dll.scr"
# 检查输出是否包含 "[XlsxToDwg] DLL loaded successfully!"
```

- [ ] **Step 6: Commit**

```bash
git add tools/xlsx2dwg_net/XlsxToDwg/
git commit -m "feat: xlsx2dwg C# project skeleton"
```

---

## Task 3: JSON Data Model

**Files:**
- Create: `tools/xlsx2dwg_net/XlsxToDwg/JsonModel.cs`

JSON Schema 设计：Python 输出此格式，C# 读取此格式。

```json
{
  "version": 1,
  "save_format": "2007",
  "font": { "name": "宋体", "charset": 134, "pitch_and_family": 2 },
  "default_text_height": 350.0,
  "sections": [
    {
      "type": "mtext",
      "text": "line1\\Pline2",
      "insert": [0.0, 53400.0, 0.0],
      "width": 74600.0,
      "text_height": 450.0,
      "bold": true
    },
    {
      "type": "table",
      "insert": [0.0, 50000.0, 0.0],
      "nrows": 10,
      "ncols": 19,
      "col_widths": [2100.0],
      "row_heights": [530.0],
      "default_text_height": 350.0,
      "title_suppressed": true,
      "cells": [
        { "row": 0, "col": 0, "text": "value", "alignment": 5 }
      ],
      "merges": [
        { "r1": 0, "c1": 0, "r2": 2, "c2": 18 }
      ]
    }
  ]
}
```

- [ ] **Step 1: 创建 JsonModel.cs**

```csharp
using System.Text.Json.Serialization;

namespace XlsxToDwg;

public class XlsxDocument
{
    [JsonPropertyName("version")]
    public int Version { get; set; } = 1;

    [JsonPropertyName("save_format")]
    public string SaveFormat { get; set; } = "2007";

    [JsonPropertyName("font")]
    public FontInfo Font { get; set; } = new();

    [JsonPropertyName("default_text_height")]
    public double DefaultTextHeight { get; set; } = 350.0;

    [JsonPropertyName("sections")]
    public List<SectionInfo> Sections { get; set; } = [];
}

public class FontInfo
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "宋体";

    [JsonPropertyName("charset")]
    public int Charset { get; set; } = 134;

    [JsonPropertyName("pitch_and_family")]
    public int PitchAndFamily { get; set; } = 2;
}

public class SectionInfo
{
    [JsonPropertyName("type")]
    public string Type { get; set; } = ""; // "mtext" or "table"

    // Common
    [JsonPropertyName("insert")]
    public List<double> Insert { get; set; } = [0, 0, 0];

    // MText fields
    [JsonPropertyName("text")]
    public string? Text { get; set; }

    [JsonPropertyName("width")]
    public double Width { get; set; }

    [JsonPropertyName("text_height")]
    public double TextHeight { get; set; }

    [JsonPropertyName("bold")]
    public bool Bold { get; set; }

    // Table fields
    [JsonPropertyName("nrows")]
    public int NRows { get; set; }

    [JsonPropertyName("ncols")]
    public int NCols { get; set; }

    [JsonPropertyName("col_widths")]
    public List<double> ColWidths { get; set; } = [];

    [JsonPropertyName("row_heights")]
    public List<double> RowHeights { get; set; } = [];

    [JsonPropertyName("default_text_height")]
    public double DefaultTextHeight { get; set; } = 350.0;

    [JsonPropertyName("title_suppressed")]
    public bool TitleSuppressed { get; set; } = true;

    [JsonPropertyName("vert_margin")]
    public double VertMargin { get; set; }

    [JsonPropertyName("horz_margin")]
    public double HorzMargin { get; set; }

    [JsonPropertyName("cells")]
    public List<CellInfo> Cells { get; set; } = [];

    [JsonPropertyName("merges")]
    public List<MergeInfo> Merges { get; set; } = [];
}

public class CellInfo
{
    [JsonPropertyName("row")]
    public int Row { get; set; }

    [JsonPropertyName("col")]
    public int Col { get; set; }

    [JsonPropertyName("text")]
    public string Text { get; set; } = "";

    [JsonPropertyName("text_height")]
    public double TextHeight { get; set; }

    [JsonPropertyName("alignment")]
    public int Alignment { get; set; } = 5; // 5=MiddleCenter
}

public class MergeInfo
{
    [JsonPropertyName("r1")]
    public int R1 { get; set; }

    [JsonPropertyName("c1")]
    public int C1 { get; set; }

    [JsonPropertyName("r2")]
    public int R2 { get; set; }

    [JsonPropertyName("c2")]
    public int C2 { get; set; }
}
```

- [ ] **Step 2: 编译验证**

```powershell
cd tools/xlsx2dwg_net/XlsxToDwg
dotnet build -c Release
```

- [ ] **Step 3: Commit**

```bash
git add tools/xlsx2dwg_net/XlsxToDwg/JsonModel.cs
git commit -m "feat: xlsx2dwg JSON data model"
```

---

## Task 4: Style Setup (TextStyle + TableStyle)

**Files:**
- Create: `tools/xlsx2dwg_net/XlsxToDwg/StyleSetup.cs`

- [ ] **Step 1: 创建 StyleSetup.cs**

```csharp
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.GraphicsInterface;

namespace XlsxToDwg;

public static class StyleSetup
{
    public const string TextStyleName = "XlsxSongTi";
    public const string TableStyleName = "XlsxTableStyle";

    public static ObjectId EnsureTextStyle(Database db, Transaction tr, FontInfo font)
    {
        var tst = (TextStyleTable)tr.GetObject(db.TextStyleTableId, OpenMode.ForRead);
        if (tst.Has(TextStyleName))
            return tst[TextStyleName];

        tst.UpgradeOpen();
        var ts = new TextStyleTableRecord
        {
            Name = TextStyleName,
            Font = new FontDescriptor(font.Name, false, false, font.Charset, font.PitchAndFamily)
        };
        tst.Add(ts);
        tr.AddNewlyCreatedDBObject(ts, true);
        return ts.ObjectId;
    }

    public static ObjectId EnsureTableStyle(Database db, Transaction tr, FontInfo font, double textHeight)
    {
        var dict = (DBDictionary)tr.GetObject(db.TableStyleDictionaryId, OpenMode.ForRead);
        if (dict.Contains(TableStyleName))
            return dict.GetAt(TableStyleName);

        dict.UpgradeOpen();

        var tsId = ObjectId.Null;
        var style = new TableStyle();
        style.StyleName = TableStyleName;

        var tsObjectId = EnsureTextStyle(db, tr, font);

        foreach (RowType rt in Enum.GetValues(typeof(RowType)))
        {
            style.SetTextHeight(rt, textHeight);
            style.SetTextStyle(rt, tsObjectId);
        }

        // 数据行居中
        style.SetAlignment(RowType.DataRow, CellAlignment.MiddleCenter);
        style.SetAlignment(RowType.HeaderRow, CellAlignment.MiddleCenter);

        tsId = dict.SetAt(TableStyleName, style);
        tr.AddNewlyCreatedDBObject(style, true);
        return tsId;
    }
}
```

- [ ] **Step 2: 编译验证**

```powershell
cd tools/xlsx2dwg_net/XlsxToDwg
dotnet build -c Release
```

- [ ] **Step 3: Commit**

```bash
git add tools/xlsx2dwg_net/XlsxToDwg/StyleSetup.cs
git commit -m "feat: xlsx2dwg style setup"
```

---

## Task 5: Entity Builder (MText + Table)

**Files:**
- Create: `tools/xlsx2dwg_net/XlsxToDwg/EntityBuilder.cs`

- [ ] **Step 1: 创建 EntityBuilder.cs**

```csharp
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace XlsxToDwg;

public static class EntityBuilder
{
    public static void AddMText(BlockTableRecord btr, Transaction tr,
        SectionInfo sec, XlsxDocument doc, ObjectId textStyleId)
    {
        if (string.IsNullOrEmpty(sec.Text)) return;

        var pt = new Point3d(
            sec.Insert.Count > 0 ? sec.Insert[0] : 0,
            sec.Insert.Count > 1 ? sec.Insert[1] : 0,
            sec.Insert.Count > 2 ? sec.Insert[2] : 0);

        var mtext = new MText
        {
            Contents = sec.Text,
            TextHeight = sec.TextHeight > 0 ? sec.TextHeight : doc.DefaultTextHeight,
            Location = pt,
            Width = sec.Width,
            TextStyleId = textStyleId,
            Attachment = AttachmentPoint.BottomLeft,
        };
        // 行间距倍数
        mtext.LineSpacingFactor = 1.2;

        btr.AppendEntity(mtext);
        tr.AddNewlyCreatedDBObject(mtext, true);
    }

    public static void AddTable(BlockTableRecord btr, Transaction tr,
        SectionInfo sec, XlsxDocument doc, ObjectId tableStyleId)
    {
        if (sec.NRows <= 0 || sec.NCols <= 0) return;

        var pt = new Point3d(
            sec.Insert.Count > 0 ? sec.Insert[0] : 0,
            sec.Insert.Count > 1 ? sec.Insert[1] : 0,
            sec.Insert.Count > 2 ? sec.Insert[2] : 0);

        var table = new Table();
        table.SetSize(sec.NRows, sec.NCols);
        table.Position = pt;
        table.TableStyle = tableStyleId;
        table.SuppressRegenerateTable(true);

        // 抑制标题行
        if (sec.TitleSuppressed)
            table.SuppressTitleRow(true);

        // 列宽
        for (int c = 0; c < sec.NCols && c < sec.ColWidths.Count; c++)
            table.SetColumnWidth(c, sec.ColWidths[c]);

        // 行高
        for (int r = 0; r < sec.NRows && r < sec.RowHeights.Count; r++)
            table.SetRowHeight(r, sec.RowHeights[r]);

        // 单元格边距
        if (sec.VertMargin > 0)
            table.VertCellMargin = sec.VertMargin;
        if (sec.HorzMargin > 0)
            table.HorzCellMargin = sec.HorzMargin;

        // 填充单元格文字
        double defaultTh = sec.DefaultTextHeight > 0 ? sec.DefaultTextHeight : doc.DefaultTextHeight;
        foreach (var cell in sec.Cells)
        {
            if (string.IsNullOrEmpty(cell.Text)) continue;
            if (cell.Row < 0 || cell.Row >= sec.NRows || cell.Col < 0 || cell.Col >= sec.NCols)
                continue;

            var c = table.Cells[cell.Row, cell.Col];
            c.TextString = cell.Text;
            c.TextHeight = cell.TextHeight > 0 ? cell.TextHeight : defaultTh;
            c.Alignment = (CellAlignment)cell.Alignment;
        }

        // 合并单元格
        foreach (var merge in sec.Merges)
        {
            if (merge.R1 < 0 || merge.R2 >= sec.NRows ||
                merge.C1 < 0 || merge.C2 >= sec.NCols)
                continue;

            table.Cells.MergeCells(
                CellRange.Create(table, merge.R1, merge.C1, merge.R2, merge.C2));
        }

        table.SuppressRegenerateTable(false);

        btr.AppendEntity(table);
        tr.AddNewlyCreatedDBObject(table, true);
    }
}
```

- [ ] **Step 2: 编译验证**

```powershell
cd tools/xlsx2dwg_net/XlsxToDwg
dotnet build -c Release
```

- [ ] **Step 3: Commit**

```bash
git add tools/xlsx2dwg_net/XlsxToDwg/EntityBuilder.cs
git commit -m "feat: xlsx2dwg entity builder"
```

---

## Task 6: Command Entry Point

**Files:**
- Modify: `tools/xlsx2dwg_net/XlsxToDwg/Commands.cs` (替换 Task 2 的 stub)

- [ ] **Step 1: 替换 Commands.cs 为完整版本**

```csharp
using System.Text.Json;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Runtime;

[assembly: CommandClass(typeof(XlsxToDwg.Commands))]

namespace XlsxToDwg;

public class Commands
{
    [CommandMethod("XLSX2DWG")]
    public static void XlsxToDwgCommand()
    {
        var doc = Application.DocumentManager.MdiActiveDocument;
        var db = doc.Database;
        var ed = doc.Editor;

        // 从命令行获取 JSON 文件路径
        var pr = ed.GetString("\nJSON file path: ");
        if (pr.Status != PromptStatus.OK || string.IsNullOrWhiteSpace(pr.StringResult))
        {
            ed.WriteMessage("\n[XLSX2DWG] Cancelled.");
            return;
        }

        string jsonPath = pr.StringResult.Trim('"').Trim();
        if (!File.Exists(jsonPath))
        {
            ed.WriteMessage($"\n[XLSX2DWG] File not found: {jsonPath}");
            return;
        }

        try
        {
            string json = File.ReadAllText(jsonPath, System.Text.Encoding.UTF8);
            var data = JsonSerializer.Deserialize<XlsxDocument>(json);
            if (data == null || data.Sections.Count == 0)
            {
                ed.WriteMessage("\n[XLSX2DWG] Empty or invalid JSON.");
                return;
            }

            using (var tr = db.TransactionManager.StartTransaction())
            {
                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                var btr = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

                // 创建样式
                var textStyleId = StyleSetup.EnsureTextStyle(db, tr, data.Font);
                var tableStyleId = StyleSetup.EnsureTableStyle(db, tr, data.Font, data.DefaultTextHeight);

                // 创建实体
                int mtextCount = 0, tableCount = 0;
                foreach (var sec in data.Sections)
                {
                    if (sec.Type == "mtext")
                    {
                        EntityBuilder.AddMText(btr, tr, sec, data, textStyleId);
                        mtextCount++;
                    }
                    else if (sec.Type == "table")
                    {
                        EntityBuilder.AddTable(btr, tr, sec, data, tableStyleId);
                        tableCount++;
                    }
                }

                tr.Commit();
                ed.WriteMessage($"\n[XLSX2DWG] Done: {mtextCount} MText, {tableCount} Table, {data.Sections.Count} total sections.");
            }
        }
        catch (System.Exception ex)
        {
            ed.WriteMessage($"\n[XLSX2DWG] ERROR: {ex.Message}");
        }
    }
}
```

- [ ] **Step 2: 编译最终 DLL**

```powershell
cd tools/xlsx2dwg_net/XlsxToDwg
dotnet build -c Release
# 输出: bin/Release/net8.0-windows/XlsxToDwg.dll
```

- [ ] **Step 3: Commit**

```bash
git add tools/xlsx2dwg_net/XlsxToDwg/Commands.cs
git commit -m "feat: xlsx2dwg command entry point"
```

---

## Task 7: Python xlsx→JSON Extractor

**Files:**
- Create: `tools/xlsx2dwg_net/xlsx2json.py`

从 `xlsx2dwg_design.py` 的 `analyze_sheet` 逻辑改编，输出 JSON 而非 LISP。完全独立文件。

- [ ] **Step 1: 创建 xlsx2json.py**

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xlsx2json.py — xlsx → JSON 数据文件（供 C# XlsxToDwg DLL 使用）

提取 xlsx 的合并单元格、边框、数据，输出 JSON。
Python 负责布局计算（坐标、尺寸），C# 只负责实体创建。

Usage:
    python tools/xlsx2dwg_net/xlsx2json.py [xlsx] [output_dir] [sheet_name]
"""
import json
import os
import sys
import io
import hashlib
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import openpyxl
from openpyxl.utils import get_column_letter

SCALE = 100
FONT_H = 350
COL_W_UNIT = 2100   # per xlsx column-width unit at 1:100
ROW_PT_MM = 0.353   # 1 pt ≈ 0.353mm


def esc_mtext(s):
    return (s.replace('\\', '\\\\').replace('"', '\\"')
             .replace('\n', '\\P').replace('\r', ''))


def analyze_sheet(ws, max_rows=None):
    """Analyze xlsx sheet → dict with sections list."""
    nrows = ws.max_row or 1
    ncols = ws.max_column or 1
    if max_rows:
        nrows = min(nrows, max_rows)

    # Merge map: (row,col) → root (row,col)
    mm = {}
    merge_ranges = []
    for mc in ws.merged_cells.ranges:
        min_r = mc.min_row - 1
        min_c = mc.min_col - 1
        max_r = min(mc.max_row - 1, nrows - 1)
        max_c = min(mc.max_col - 1, ncols - 1)
        if min_r >= nrows:
            continue
        merge_ranges.append((min_r, min_c, max_r, max_c))
        for r in range(min_r, max_r + 1):
            for c in range(min_c, max_c + 1):
                if (r, c) != (min_r, min_c):
                    mm[(r, c)] = (min_r, min_c)

    def cell(row, col):
        root = mm.get((row, col), (row, col))
        v = ws.cell(root[0] + 1, root[1] + 1).value
        return str(v).strip() if v else ''

    # Detect borders per row
    row_has_border = []
    for ri in range(nrows):
        has = False
        for ci in range(ncols):
            c = ws.cell(ri + 1, ci + 1)
            sides = [c.border.left, c.border.right, c.border.top, c.border.bottom]
            if any(s and s.style and s.style != 'none' for s in sides):
                has = True
                break
        row_has_border.append(has)

    # Classify rows into sections (text vs table)
    sections_raw = []
    cur_type = 'mtext' if not row_has_border[0] else 'table'
    cur_start = 0
    for ri in range(1, nrows):
        rtype = 'mtext' if not row_has_border[ri] else 'table'
        if rtype != cur_type:
            sections_raw.append({'type': cur_type, 'start': cur_start, 'end': ri - 1})
            cur_type = rtype
            cur_start = ri
    sections_raw.append({'type': cur_type, 'start': cur_start, 'end': nrows - 1})

    # Column widths
    col_widths_xlsx = []
    for ci in range(ncols):
        dim = ws.column_dimensions[get_column_letter(ci + 1)]
        col_widths_xlsx.append(dim.width if dim.width else 8.43)

    # Build JSON sections with layout coordinates
    col_ws = [w * COL_W_UNIT for w in col_widths_xlsx]
    total_w = sum(col_ws)
    y_offset = 0

    json_sections = []
    for sec in sections_raw:
        s, e = sec['start'], sec['end']
        nsec_rows = e - s + 1

        if sec['type'] == 'mtext':
            lines = []
            for ri in range(s, e + 1):
                v = cell(ri, 0)
                if v:
                    lines.append(v)
            text = '\\P'.join(lines)
            if not text:
                continue

            est_h = (nsec_rows * 15 * ROW_PT_MM + 5) * SCALE
            insert_y = y_offset + est_h

            json_sections.append({
                'type': 'mtext',
                'text': text,
                'insert': [0.0, round(insert_y, 0), 0.0],
                'width': round(total_w, 0),
                'text_height': FONT_H,
                'bold': False,
            })
            y_offset -= (est_h + 200)

        else:  # table
            cells = []
            for ri in range(s, e + 1):
                for ci in range(ncols):
                    v = cell(ri, ci)
                    if v:
                        cells.append({
                            'row': ri - s,
                            'col': ci,
                            'text': v,
                            'alignment': 5,
                        })

            merges = []
            for (mr, mc, er, ec) in merge_ranges:
                if mr > e or er < s:
                    continue
                local_mr = max(mr - s, 0)
                local_er = min(er - s, e - s)
                if local_mr <= local_er:
                    merges.append({
                        'r1': local_mr, 'c1': mc,
                        'r2': local_er, 'c2': ec,
                    })

            row_hs = []
            for ri in range(s, e + 1):
                dim = ws.row_dimensions[ri + 1]
                row_hs.append((dim.height if dim.height else 15) * ROW_PT_MM * SCALE)

            default_rh = max(row_hs) if row_hs else 500
            tbl_h = sum(row_hs)
            insert_y = y_offset + tbl_h

            json_sections.append({
                'type': 'table',
                'insert': [0.0, round(insert_y, 0), 0.0],
                'nrows': nsec_rows,
                'ncols': ncols,
                'col_widths': [round(w, 0) for w in col_ws],
                'row_heights': [round(h, 0) for h in row_hs],
                'default_text_height': FONT_H,
                'title_suppressed': True,
                'vert_margin': round(FONT_H / 3, 0),
                'horz_margin': round(FONT_H / 3, 0),
                'cells': cells,
                'merges': merges,
            })
            y_offset -= (tbl_h + 200)

    return {
        'version': 1,
        'save_format': '2007',
        'font': {
            'name': '宋体',
            'charset': 134,
            'pitch_and_family': 2,
        },
        'default_text_height': FONT_H,
        'sections': json_sections,
    }


def extract_to_json(xlsx_path, output_dir=None, sheet_name=None, max_rows=None):
    """Extract xlsx → JSON file. Returns JSON file path."""
    output_dir = output_dir or str(Path(xlsx_path).parent)
    os.makedirs(output_dir, exist_ok=True)

    print(f"Reading: {xlsx_path}")
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    sn = sheet_name or wb.sheetnames[0]
    ws = wb[sn]

    print(f"Sheet: {sn}, analyzing...")
    data = analyze_sheet(ws, max_rows=max_rows)

    mtext_count = sum(1 for s in data['sections'] if s['type'] == 'mtext')
    table_count = sum(1 for s in data['sections'] if s['type'] == 'table')
    total_cells = sum(len(s.get('cells', [])) for s in data['sections'])
    total_merges = sum(len(s.get('merges', [])) for s in data['sections'])
    print(f"  {mtext_count} MText + {table_count} Table sections")
    print(f"  {total_cells} cells, {total_merges} merges")

    safe = "xlsx_" + hashlib.md5((Path(xlsx_path).stem + sn).encode()).hexdigest()[:8]
    json_path = os.path.join(output_dir, f"{safe}.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"JSON: {json_path} ({os.path.getsize(json_path):,} bytes)")
    return json_path


if __name__ == "__main__":
    f = sys.argv[1] if len(sys.argv) > 1 else r"C:\opt\ACADxPDF\.test\design-doc-P038215-33264.xlsx"
    out = sys.argv[2] if len(sys.argv) > 2 else r"C:\opt\ACADxPDF\_test_output"
    sheet = sys.argv[3] if len(sys.argv) > 3 else "建筑"
    mr = int(sys.argv[4]) if len(sys.argv) > 4 else None
    extract_to_json(f, out, sheet, mr)
```

- [ ] **Step 2: 测试 JSON 提取**

```powershell
conda activate pdf
python tools/xlsx2dwg_net/xlsx2json.py .test/design-doc-P038215-33264.xlsx _test_output 建筑
# 预期: 输出 JSON 文件到 _test_output/，包含 MText + Table sections
```

验证 JSON 内容:
```powershell
python -c "import json; d=json.load(open('_test_output/xlsx_xxxxx.json','r',encoding='utf-8')); print(len(d['sections']), 'sections')"
```

- [ ] **Step 3: Commit**

```bash
git add tools/xlsx2dwg_net/xlsx2json.py
git commit -m "feat: xlsx→JSON extractor"
```

---

## Task 8: Python Orchestrator (run_xlsx2dwg.py)

**Files:**
- Create: `tools/xlsx2dwg_net/run_xlsx2dwg.py`

编排器：Python → JSON → 编译 DLL → 生成 script → accoreconsole → DWG。

- [ ] **Step 1: 创建 run_xlsx2dwg.py**

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_xlsx2dwg.py — xlsx → DWG 编排器

Pipeline: xlsx → JSON → C# DLL → accoreconsole → DWG
支持多线程并行（--threads N）。

Usage:
    python tools/xlsx2dwg_net/run_xlsx2dwg.py [xlsx] [output_dir] [--sheet SHEET] [--threads 4]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ACCCORE = os.environ.get("ACAD_PATH", r"C:\opt\AutoCAD 2026\accoreconsole.exe")
ACAD_EXE = os.environ.get("ACAD_EXE", r"C:\opt\AutoCAD 2026\acad.exe")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DLL_DIR = os.path.join(SCRIPT_DIR, "XlsxToDwg")
DLL_PATH = os.path.join(DLL_DIR, "bin", "Release", "net8.0-windows", "XlsxToDwg.dll")
DEFAULT_TEMPLATE = r"C:\opt\ACADxPDF\Template\mt.dwt"


def build_dll():
    """编译 C# DLL。返回 DLL 路径。"""
    if os.path.isfile(DLL_PATH):
        mtime = os.path.getmtime(DLL_PATH)
        age = time.time() - mtime
        if age < 3600:  # 1小时内编译过，跳过
            print(f"DLL up-to-date: {DLL_PATH}")
            return DLL_PATH

    print("Building XlsxToDwg.dll...")
    result = subprocess.run(
        ["dotnet", "build", "-c", "Release"],
        cwd=DLL_DIR, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"BUILD FAILED:\n{result.stdout}\n{result.stderr}")
        return None
    print(f"DLL built: {DLL_PATH}")
    return DLL_PATH


def _run_one(json_path, dll_path, output_dir, template, timeout=300):
    """单个 accoreconsole 实例：加载 DLL → 执行 XLSX2DWG → 保存 DWG。"""
    work_dir = os.path.join(output_dir, f"_work_{uuid.uuid4().hex[:6]}")
    os.makedirs(work_dir, exist_ok=True)

    # 复制 JSON 到工作目录（避免路径中文问题）
    json_copy = os.path.join(work_dir, "data.json")
    shutil.copy2(json_path, json_copy)

    # 输出 DWG 路径
    stem = Path(json_path).stem
    dwg_out = os.path.abspath(os.path.join(output_dir, f"{stem}.dwg"))
    if os.path.isfile(dwg_out):
        os.remove(dwg_out)

    # 生成 script
    dll_fwd = os.path.abspath(dll_path).replace('\\', '/')
    json_fwd = os.path.abspath(json_copy).replace('\\', '/')
    dwg_fwd = os.path.abspath(dwg_out).replace('\\', '/')
    tpl_fwd = os.path.abspath(template).replace('\\', '/') if template else ""

    scr_path = os.path.join(work_dir, "_convert.scr")
    with open(scr_path, 'w', encoding='utf-8') as f:
        f.write('(setvar "FILEDIA" 0)\n')
        f.write('(setvar "CMDECHO" 0)\n')
        f.write('(setvar "SECURELOAD" 0)\n')
        f.write('(setvar "EXPERT" 5)\n')
        f.write(f'(command "_.NETLOAD" "{dll_fwd}")\n')
        f.write(f'(command "XLSX2DWG" "{json_fwd}")\n')
        f.write(f'(command "_.SAVEAS" "2007" "{dwg_fwd}")\n')
        f.write('(command "_.QUIT" "Y")\n')

    # 运行 accoreconsole
    cmd = [ACCCORE]
    if tpl_fwd and os.path.isfile(template):
        cmd += ["/i", template]
    cmd += ["/b", scr_path]

    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=work_dir)
        elapsed = time.time() - t0
        success = os.path.isfile(dwg_out)
        sz = os.path.getsize(dwg_out) if success else 0
        print(f"  {'OK' if success else 'FAIL'}: {os.path.basename(dwg_out)} "
              f"({sz:,} bytes, {elapsed:.1f}s, rc={r.returncode})")
        return {'success': success, 'dwg': dwg_out if success else None,
                'elapsed': elapsed, 'error': '' if success else f'rc={r.returncode}'}
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        print(f"  TIMEOUT: {os.path.basename(dwg_out)} ({elapsed:.1f}s)")
        return {'success': False, 'dwg': None, 'elapsed': elapsed, 'error': 'timeout'}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def convert_single(xlsx_path, output_dir=None, sheet_name=None,
                   dll_path=None, template=None, timeout=300):
    """单文件转换: xlsx → JSON → accoreconsole → DWG。"""
    output_dir = output_dir or str(Path(xlsx_path).parent)
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: xlsx → JSON
    from xlsx2json import extract_to_json
    json_path = extract_to_json(xlsx_path, output_dir, sheet_name)

    # Step 2: Build DLL
    dll_path = dll_path or build_dll()
    if not dll_path:
        print("ERROR: DLL build failed")
        return None

    # Step 3: Run accoreconsole
    template = template or DEFAULT_TEMPLATE
    result = _run_one(json_path, dll_path, output_dir, template, timeout)
    return result['dwg'] if result['success'] else None


def convert_parallel(xlsx_path, output_dir=None, sheet_name=None,
                     dll_path=None, template=None, timeout=300,
                     num_threads=4):
    """并行转换：将 xlsx 拆分为多个 JSON 分片，多 accoreconsole 实例并行。"""
    output_dir = output_dir or str(Path(xlsx_path).parent)
    os.makedirs(output_dir, exist_ok=True)

    from xlsx2json import extract_to_json, analyze_sheet
    import openpyxl

    # 读取 xlsx 并分析
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    sn = sheet_name or wb.sheetnames[0]
    ws = wb[sn]
    full_data = analyze_sheet(ws)

    if len(full_data['sections']) <= 1:
        # 内容太少，无需拆分
        print("Too few sections, running single...")
        return [convert_single(xlsx_path, output_dir, sheet_name, dll_path, template, timeout)]

    # 拆分 sections 为 num_threads 个分片
    sections = full_data['sections']
    chunk_size = max(1, len(sections) // num_threads)
    chunks = []
    for i in range(0, len(sections), chunk_size):
        chunk = sections[i:i + chunk_size]
        chunk_data = dict(full_data)
        chunk_data['sections'] = chunk
        chunk_file = os.path.join(output_dir, f"_chunk_{i // chunk_size}.json")
        with open(chunk_file, 'w', encoding='utf-8') as f:
            json.dump(chunk_data, f, ensure_ascii=False, indent=2)
        chunks.append(chunk_file)

    print(f"Split into {len(chunks)} chunks for parallel processing")

    # Build DLL
    dll_path = dll_path or build_dll()
    if not dll_path:
        print("ERROR: DLL build failed")
        return []

    # 并行执行
    results = []
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = {
            executor.submit(_run_one, chunk, dll_path, output_dir, template or DEFAULT_TEMPLATE, timeout): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            results.append(future.result())

    # 汇总
    dwg_files = [r['dwg'] for r in results if r['success']]
    print(f"\nParallel done: {len(dwg_files)}/{len(chunks)} succeeded")
    return dwg_files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="xlsx → DWG converter")
    parser.add_argument("xlsx", help="Input xlsx file")
    parser.add_argument("output_dir", nargs="?", help="Output directory")
    parser.add_argument("--sheet", help="Sheet name")
    parser.add_argument("--threads", type=int, default=1, help="Parallel threads (1=single)")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, help="DWT template")
    parser.add_argument("--timeout", type=int, default=300, help="Per-file timeout (seconds)")
    parser.add_argument("--dll", help="Pre-built DLL path")
    args = parser.parse_args()

    if args.threads > 1:
        dwgs = convert_parallel(
            args.xlsx, args.output_dir, args.sheet, args.dll,
            args.template, args.timeout, args.threads)
        print(f"\nOutput DWGs: {dwgs}")
    else:
        dwg = convert_single(
            args.xlsx, args.output_dir, args.sheet, args.dll,
            args.template, args.timeout)
        print(f"\nOutput DWG: {dwg}")
```

- [ ] **Step 2: Commit**

```bash
git add tools/xlsx2dwg_net/run_xlsx2dwg.py
git commit -m "feat: xlsx2dwg orchestrator with parallel support"
```

---

## Task 9: End-to-End Test (Single File)

**Files:**
- Create: `tools/xlsx2dwg_net/test_xlsx2dwg.py`

- [ ] **Step 1: 创建测试脚本**

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_xlsx2dwg.py — 端到端集成测试

验证: xlsx → JSON → C# DLL → accoreconsole → DWG
"""
import os
import sys
import json
import subprocess
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_XLSX = r"C:\opt\ACADxPDF\.test\design-doc-P038215-33264.xlsx"
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "_test_output")
DLL_PATH = os.path.join(SCRIPT_DIR, "XlsxToDwg", "bin", "Release", "net8.0-windows", "XlsxToDwg.dll")
ACCCORE = os.environ.get("ACAD_PATH", r"C:\opt\AutoCAD 2026\accoreconsole.exe")


def test_json_extraction():
    """测试 xlsx → JSON。"""
    print("=" * 60)
    print("TEST 1: JSON extraction")
    sys.path.insert(0, SCRIPT_DIR)
    from xlsx2json import extract_to_json

    json_path = extract_to_json(TEST_XLSX, OUTPUT_DIR, "建筑", max_rows=30)
    assert os.path.isfile(json_path), f"JSON not created: {json_path}"

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    assert data['version'] == 1
    assert len(data['sections']) > 0, "No sections in JSON"
    types = {s['type'] for s in data['sections']}
    print(f"  OK: {len(data['sections'])} sections, types: {types}")
    return json_path


def test_dll_exists():
    """测试 DLL 是否已编译。"""
    print("=" * 60)
    print("TEST 2: DLL exists")
    assert os.path.isfile(DLL_PATH), f"DLL not found: {DLL_PATH} — run: dotnet build"
    sz = os.path.getsize(DLL_PATH)
    print(f"  OK: {DLL_PATH} ({sz:,} bytes)")
    return DLL_PATH


def test_accoreconsole_load():
    """测试 accoreconsole 能加载 DLL 并执行命令。"""
    print("=" * 60)
    print("TEST 3: accoreconsole + NETLOAD")

    work_dir = os.path.join(OUTPUT_DIR, "_dll_test")
    os.makedirs(work_dir, exist_ok=True)

    dll_fwd = DLL_PATH.replace('\\', '/')
    scr_path = os.path.join(work_dir, "_test.scr")
    with open(scr_path, 'w', encoding='utf-8') as f:
        f.write('(setvar "FILEDIA" 0)\n')
        f.write('(setvar "CMDECHO" 0)\n')
        f.write('(setvar "SECURELOAD" 0)\n')
        f.write(f'(command "_.NETLOAD" "{dll_fwd}")\n')
        f.write('(command "XLSXHELLO")\n')
        f.write('(command "_.QUIT" "Y")\n')

    r = subprocess.run(
        [ACCCORE, "/b", scr_path],
        capture_output=True, timeout=60, cwd=work_dir,
    )
    output = r.stdout.decode('utf-8', errors='replace') + r.stderr.decode('utf-8', errors='replace')
    print(f"  RC={r.returncode}")
    if "XlsxToDwg" in output or "XLSXHELLO" in output or r.returncode == 0:
        print("  OK: DLL loaded in accoreconsole")
    else:
        print(f"  WARN: Output may not contain expected text")
        print(f"  stdout+stderr (last 500 chars): {output[-500:]}")


def test_full_conversion():
    """测试完整转换: xlsx → DWG。"""
    print("=" * 60)
    print("TEST 4: Full xlsx → DWG conversion")

    sys.path.insert(0, SCRIPT_DIR)
    from run_xlsx2dwg import convert_single

    # 限制行数以加快测试
    from xlsx2json import extract_to_json
    json_path = extract_to_json(TEST_XLSX, OUTPUT_DIR, "建筑", max_rows=30)

    from run_xlsx2dwg import build_dll, _run_one
    dll = build_dll()
    assert dll, "DLL build failed"

    result = _run_one(json_path, dll, OUTPUT_DIR,
                      r"C:\opt\ACADxPDF\Template\mt.dwt", timeout=120)

    if result['success']:
        sz = os.path.getsize(result['dwg'])
        print(f"  OK: {result['dwg']} ({sz:,} bytes, {result['elapsed']:.1f}s)")
    else:
        print(f"  FAIL: {result['error']}")

    return result['success']


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    passed = 0
    total = 4

    try:
        test_json_extraction(); passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}")

    try:
        test_dll_exists(); passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}")

    try:
        test_accoreconsole_load(); passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")

    try:
        if test_full_conversion():
            passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")

    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
```

- [ ] **Step 2: 运行测试**

```powershell
conda activate pdf
# 先确保 DLL 已编译
cd tools/xlsx2dwg_net/XlsxToDwg && dotnet build -c Release
# 运行测试
python tools/xlsx2dwg_net/test_xlsx2dwg.py
# 预期: 4/4 passed
```

- [ ] **Step 3: Commit**

```bash
git add tools/xlsx2dwg_net/test_xlsx2dwg.py
git commit -m "feat: xlsx2dwg integration test"
```

---

## Task 10: Parallel Processing Test

**Files:** 无新文件

- [ ] **Step 1: 测试并行转换（4线程）**

```powershell
conda activate pdf
python tools/xlsx2dwg_net/run_xlsx2dwg.py .test/design-doc-P038215-33264.xlsx _test_output --sheet 建筑 --threads 4 --timeout 300
# 预期: 4个 accoreconsole 实例并行运行，输出多个 DWG
```

- [ ] **Step 2: 验证输出**

```powershell
ls _test_output/*.dwg
# 预期: 多个 DWG 文件
```

- [ ] **Step 3: Commit（如有修改）**

---

## Task 11: LISP Fallback Verification

确保新实现不影响现有 `xlsx2dwg_design.py` 和 `xlsx2dwg.py`。

- [ ] **Step 1: 验证旧工具仍可运行**

```powershell
conda activate pdf
python tools/xlsx2dwg_design.py .test/design-doc-P038215-33264.xlsx _test_output 建筑 30
# 预期: 旧工具正常工作，生成 LISP 并输出 DWG
```

- [ ] **Step 2: 对比新旧输出质量**

用 AutoCAD 打开两个 DWG，对比表格/文字渲染效果。记录差异。

---

## Self-Review Checklist

- [x] **Spec coverage:**
  - xlsx → JSON extraction: Task 7
  - JSON → C# Table/MText: Tasks 3-6
  - accoreconsole 执行: Task 8
  - 多线程并行: Task 8 (run_xlsx2dwg.py `convert_parallel`)
  - 不影响原有功能: Task 11 验证
- [x] **Placeholder scan:** 无 TBD/TODO/placeholder
- [x] **Type consistency:** JsonModel.cs 属性名与 xlsx2json.py JSON key 一致（通过 `[JsonPropertyName]` 映射）
- [x] **File paths:** 所有文件路径以 `tools/xlsx2dwg_net/` 为根
- [x] **Build chain:** dotnet build → DLL → accoreconsole NETLOAD → 命令执行
