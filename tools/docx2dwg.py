#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
docx2dwg.py — Convert docx tables to DWG via acad.exe + LISP vla-AddTable

Pipeline:
  docx → Python extract → LISP (vla-AddTable/MTEXT) → acad.exe → DWG 2007

Usage:
    python tools/docx2dwg.py [docx_path]
"""

import os
import sys
import subprocess
import ezdxf
from pathlib import Path
from docx import Document

ACAD_EXE = os.environ.get("ACAD_EXE", r"C:\opt\AutoCAD 2026\acad.exe")
ACCCORE = os.environ.get("ACAD_PATH", r"C:\opt\AutoCAD 2026\accoreconsole.exe")


# ── LISP 字符串转义 ──────────────────────────────────────────

def esc(s):
    """Escape for AutoLISP string literal (table cell text, keeps \\n)."""
    return (s.replace('\\', '\\\\')
             .replace('"', '\\"')
             .replace('\n', '\\n')
             .replace('\r', ''))


def esc_mtext(s):
    """Escape for MTEXT content (\\P = paragraph break)."""
    return (s.replace('\\', '\\\\')
             .replace('"', '\\"')
             .replace('\n', '\\\\P')
             .replace('\r', ''))


def _skip_set(merges):
    skip = set()
    for r1, r2, c1, c2 in merges:
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if r != r1 or c != c1:
                    skip.add((r, c))
    return skip


# ── LISP 生成 ────────────────────────────────────────────────

# A1 图幅可用区域 (mm)
A1_WIDTH = 800    # 841 - 边距
A1_HEIGHT = 554   # 594 - 边距
PAGE_MARGIN_Y = 20  # 页间距


def generate_lisp(data, output_dwg):
    """Generate LISP that creates ACAD_TABLE + MTEXT, saves DWG 2007."""
    L = []
    def a(s): L.append(s)

    a('(vl-load-com)')
    a('(defun c:Docx2Dwg (/ acad doc msp tbl pt y tx nr nc pageY)')
    a('  (setq acad (vlax-get-acad-object))')
    a('  (setq doc (vla-get-ActiveDocument acad))')
    a('  (setq msp (vla-get-ModelSpace doc))')
    a('  (setvar "FILEDIA" 0)')
    a('  (setvar "CMDECHO" 0)')
    # Create text style lvjianst with 宋体 font
    a('  (command "_.STYLE" "lvjianst" "宋体" "0" "1" "0" "N" "N")')

    # ── MTEXT 标题段落（第一页顶部）──
    y = data.get("start_y", 830)
    for p in data["paragraphs"]:
        txt = esc_mtext(p["text"])
        h = p.get("height", 3.0)
        w = p.get("width", 800)
        a(f'  (setq tx (vla-AddMText msp (vlax-3d-point 20 {y:.1f}) {w} "{txt}"))')
        a(f'  (vla-put-Height tx {h})')
        a(f'  (vla-put-StyleName tx "lvjianst")')
        y -= p.get("spacing", 6)

    # ── 分页创建表格 ──
    pages = data["table_pages"]
    a(f'  (setq pageY {y:.1f})')

    for pi, page in enumerate(pages):
        rows = page["rows"]
        nr = len(rows)
        nc = len(rows[0]) if rows else 0
        cw = page.get("col_widths", [100] * nc)
        merges = page.get("merges", [])
        skip = _skip_set(merges)

        # 如果不是第一页，检查 Y 是否超出 A1 底部，超出则换页
        if pi > 0:
            # 估算当前表格高度：表头12 + 数据行30 * nr
            est_h = 12 + 30 * (nr - 1) if nr > 1 else 12
            a(f'  (setq pageY (- pageY {est_h + PAGE_MARGIN_Y}))')

        a(f'  (setq pt (vlax-3d-point 20 pageY))')
        a(f'  (setq tbl (vla-AddTable msp pt {nr} {nc} 10 50))')
        a('  (if tbl (princ "\\n[docx2dwg] Table OK") (princ "\\n[docx2dwg] Table FAILED"))')
        a('  (vla-put-TitleSuppressed tbl :vlax-true)')

        # Column widths
        for ci, w in enumerate(cw):
            a(f'  (vla-SetColumnWidth tbl {ci} {w:.1f})')

        # Row heights: header 15, data 30 (适应 5.0 字高)
        a(f'  (vla-SetRowHeight tbl 0 15)')
        for ri in range(1, nr):
            a(f'  (vla-SetRowHeight tbl {ri} 30)')

        # Merge cells
        for r1, r2, c1, c2 in merges:
            a(f'  (vla-MergeCells tbl {r1} {r2} {c1} {c2})')

        # Cell text + formatting (font + alignment)
        for ri, row in enumerate(rows):
            for ci, cell in enumerate(row):
                if (ri, ci) in skip:
                    continue
                txt = esc(cell)
                a(f'  (vla-SetText tbl {ri} {ci} "{txt}")')
                a(f'  (vla-SetCellTextHeight tbl {ri} {ci} 5.0)')
                a(f'  (vla-SetCellAlignment tbl {ri} {ci} 5)')

    # Save DWG 2007
    dwg_fwd = output_dwg.replace('\\', '/')
    a(f'  (setvar "FILEDIA" 0)')
    a(f'  (command "_.SAVEAS" "2007" "{dwg_fwd}")')
    a('  (princ "\\n[docx2dwg] Done.")')
    a(')')

    return '\n'.join(L)


# ── Docx 数据提取 ────────────────────────────────────────────

ROWS_PER_PAGE = 3  # 每页数据行数


def _analyze_merges(rows):
    """自动分析合并区域：连续相同内容的列需要合并。"""
    if not rows:
        return []
    nc = len(rows[0])
    merges = []

    # 对每列检测连续相同值（跳过表头行0）
    for ci in range(nc):
        start = None
        for ri in range(1, len(rows)):
            val = rows[ri][ci]
            if ri == 1:
                start = 1
            elif val == rows[ri - 1][ci] and val:
                pass  # 继续合并
            else:
                if start and ri - 1 > start and rows[start][ci]:
                    merges.append((start, ri - 1, ci, ci))
                start = ri
        # 最后一组
        if start and len(rows) - 1 > start and rows[start][ci]:
            merges.append((start, len(rows) - 1, ci, ci))

    # 表头行：列1-3 如果相同则合并
    if nc > 3:
        hdr_vals = [rows[0][ci] for ci in range(1, min(4, nc))]
        if len(set(hdr_vals)) == 1 and hdr_vals[0]:
            merges.append((0, 0, 1, min(3, nc - 1)))

    return merges


def extract_section(docx_path):
    """Extract test section: 设计依据 text + 全部分页的技术措施表。"""
    doc = Document(docx_path)

    paras = [
        {"text": "三、主要绿色技术措施", "height": 5.0, "spacing": 10},
        {"text": "（一）设计依据", "height": 4.0, "spacing": 8},
    ]
    for i in range(78, 88):
        t = doc.paragraphs[i].text.strip()
        if t:
            paras.append({"text": t, "height": 2.5, "spacing": 4.5})

    paras.append({"text": "（二）主要绿色技术措施", "height": 4.0, "spacing": 8})
    paras.append({"text": "(注：控制项达标填√，不达标填×)", "height": 2.0, "spacing": 10})

    # Table 4: 全部 106 行
    tbl = doc.tables[4]
    header = [cell.text.strip() for cell in tbl.rows[0].cells]
    data_rows = []
    for ri in range(1, len(tbl.rows)):
        cells = [cell.text.strip() for cell in tbl.rows[ri].cells]
        data_rows.append(cells)

    # 分页：每页 ROWS_PER_PAGE 行数据 + 1 行表头
    col_widths = [30, 18, 18, 18, 280, 22, 22, 340]
    pages = []
    for start in range(0, len(data_rows), ROWS_PER_PAGE):
        chunk = data_rows[start:start + ROWS_PER_PAGE]
        page_rows = [header] + chunk
        merges = _analyze_merges(page_rows)
        pages.append({
            "rows": page_rows,
            "col_widths": col_widths,
            "merges": merges,
        })

    # TEST: 限制页数（后续去掉）
    max_pages = int(os.environ.get("MAX_PAGES", "2"))
    if len(pages) > max_pages:
        pages = pages[:max_pages]

    print(f"  Total data rows: {len(data_rows)}, Pages: {len(pages)} (max_pages={max_pages})")
    return {
        "paragraphs": paras,
        "table_pages": pages,
    }


# ── 转换流程 ─────────────────────────────────────────────────

def convert(docx_path, output_dir=None):
    """Convert docx section to DWG via acad.exe."""
    output_dir = output_dir or str(Path(docx_path).parent / "_dwg_test")
    os.makedirs(output_dir, exist_ok=True)

    dwg_out = os.path.abspath(os.path.join(output_dir, "docx_output.dwg"))

    # Step 1: Create base DWG (only if not exists)
    # Remove previous output to avoid SAVEAS overwrite prompt
    if os.path.isfile(dwg_out):
        os.remove(dwg_out)

    # Step 1: Create base DWG with SimSun text style
    base_dwg = os.path.abspath(os.path.join(output_dir, "_base.dwg"))
    if not os.path.isfile(base_dwg):
        base_dxf = os.path.abspath(os.path.join(output_dir, "_base.dxf"))
        doc = ezdxf.new("R2007")
        doc.styles.add("SimSun", font="simsun.ttc")
        doc.header["$TEXTSTYLE"] = "SimSun"
        doc.saveas(base_dxf)
        base_dxf = os.path.abspath(os.path.join(output_dir, "_base.dxf"))
        ezdxf.new("R2007").saveas(base_dxf)
        scr_to_dwg = os.path.abspath(os.path.join(output_dir, "_base_to_dwg.scr"))
        base_dwg_fwd = base_dwg.replace('\\', '/')
        with open(scr_to_dwg, 'w', encoding='utf-8') as f:
            f.write('(setvar "FILEDIA" 0)\n')
            f.write('(setvar "CMDECHO" 0)\n')
            f.write(f'(command "_.SAVEAS" "2007" "{base_dwg_fwd}")\n')
            f.write('(command "_.QUIT" "Y")\n')
        cmd = f'"{ACCCORE}" /i "{base_dxf}" /s "{scr_to_dwg}"'
        print("[1/3] Creating base DWG...")
        subprocess.run(cmd, capture_output=True, timeout=60, cwd=output_dir)
        if not os.path.isfile(base_dwg):
            print("FAILED: base DWG not created")
            return None
    print(f"[1/3] Base DWG: {os.path.getsize(base_dwg):,} bytes")

    # Generate LISP (external file, GBK) + SCR (loads and calls it)
    data = extract_section(docx_path)
    lisp_content = generate_lisp(data, dwg_out)
    lsp_file = os.path.abspath(os.path.join(output_dir, "_docx2dwg.lsp"))
    scr_file = os.path.abspath(os.path.join(output_dir, "_docx2dwg.scr"))

    with open(lsp_file, 'w', encoding='gbk', errors='replace') as f:
        f.write(lisp_content)

    lsp_fwd = lsp_file.replace('\\', '/')

    with open(scr_file, 'w', encoding='utf-8') as f:
        f.write('(setvar "FILEDIA" 0)\n')
        f.write('(setvar "CMDECHO" 0)\n')
        f.write(f'(load "{lsp_fwd}")\n')
        f.write('(c:Docx2Dwg)\n')
        f.write('(command "_.QUIT" "Y")\n')

    print(f"[2/3] LISP ({len(lisp_content):,} chars) + SCR generated")

    # Step 3: Run acad.exe
    cmd = f'"{ACAD_EXE}" "{base_dwg}" /b "{scr_file}"'
    print(f"[3/3] Running acad.exe...")
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, cwd=output_dir)
    except subprocess.TimeoutExpired:
        print("TIMEOUT!")
        return None

    if os.path.isfile(dwg_out):
        sz = os.path.getsize(dwg_out)
        print(f"\nSUCCESS: {dwg_out} ({sz:,} bytes)")
        return dwg_out
    else:
        print(f"\nFAILED: {dwg_out} not created (RC={r.returncode})")
        return None


if __name__ == "__main__":
    f = sys.argv[1] if len(sys.argv) > 1 else (
        r"C:\opt\ACADxPDF\.test\附件1山西省绿色民用建筑设计专篇（发布稿） .docx"
    )
    convert(f)
