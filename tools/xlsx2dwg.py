#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xlsx2dwg.py — Convert 评分表 xlsx to DWG via acad.exe + LISP vla-AddTable

Pipeline:
  xlsx → openpyxl extract → LISP (vla-AddTable/MTEXT) → acad.exe → DWG 2007

Layout:
  - A1 图框 (841×594mm), 右下角在 (0,0), 1:100 比例
  - 每个图框内 3 列并排放表格 (每个表有独立表头)
  - 一个 DWG 可包含多个图框

Usage:
    python tools/xlsx2dwg.py [xlsx_path]
"""

import os
import sys
import subprocess
from pathlib import Path
import openpyxl

ACAD_EXE = os.environ.get("ACAD_EXE", r"C:\opt\AutoCAD 2026\acad.exe")
ACCCORE = os.environ.get("ACAD_PATH", r"C:\opt\AutoCAD 2026\accoreconsole.exe")
A1_TEMPLATE = r"C:\opt\ACADxPDF\Template\A1.dwt"

# ── Scale 1:100 ──────────────────────────────────────────────
SCALE = 100

# ── A1 图幅 (mm), 右下角在 origin ────────────────────────────
A1_W = 84100    # 841 × 100
A1_H = 59400    # 594 × 100

# 图框布局：右下角在 (0,0)
# 从右向左: 7000 标题栏区, 74600 绘图区
# 从下向上: 1000 标题栏区, 57400 绘图区
TITLE_BLOCK_R = 7000   # 右侧标题栏
TITLE_BLOCK_B = 1000   # 底部标题栏
DRAW_W = 74600
DRAW_H = 57400

DRAW_LEFT = -(TITLE_BLOCK_R + DRAW_W)   # -81600
DRAW_RIGHT = -TITLE_BLOCK_R             # -7000
DRAW_BOTTOM = TITLE_BLOCK_B             # 1000
DRAW_TOP = TITLE_BLOCK_B + DRAW_H       # 58400

# 上方留出图纸标题区域 (绿色建筑设计专篇)
TITLE_AREA_H = 5000
TABLE_TOP = DRAW_TOP - TITLE_AREA_H     # 53400

# 3 列布局
COL_COUNT = 3
COL_GAP = 1500
COL_W = (DRAW_W - COL_GAP * (COL_COUNT - 1)) // COL_COUNT


# ── LISP 字符串转义 ──────────────────────────────────────────

def esc(s):
    """Escape for AutoLISP string literal."""
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


# ── xlsx 数据提取 ─────────────────────────────────────────────

def _build_merge_map(ws):
    """Build lookup: (row, col) -> merge root (top-left cell)."""
    m = {}
    for mc in ws.merged_cells.ranges:
        for r in range(mc.min_row, mc.max_row + 1):
            for c in range(mc.min_col, mc.max_col + 1):
                m[(r, c)] = (mc.min_row, mc.min_col)
    return m


def _cell(ws, mm, row, col):
    """Get cell value with merge awareness."""
    root = mm.get((row, col), (row, col))
    v = ws.cell(root[0], root[1]).value
    return str(v).strip() if v else ''


def _find_chapters(ws, mm):
    """Find chapter boundaries from col A."""
    chapters = []
    prev_val = None
    for ri in range(2, ws.max_row + 1):
        v = _cell(ws, mm, ri, 1)
        if v and v != prev_val:
            # Check if this is a chapter number (4, 5, 6, 7, 8, 9)
            try:
                ch = int(v)
                if 4 <= ch <= 9:
                    chapters.append({'row': ri, 'num': ch, 'label': v})
            except ValueError:
                pass
            prev_val = v
    return chapters


SECTION_TYPES = {'控制项', '评分项', '加分项'}

def _get_section_type(ws, mm, row):
    """Get section type (控制项/评分项/加分项) from col B, merge-aware.

    Handles cases where col B merge spans '评分项' across rows but
    openpyxl only returns partial text per row.
    """
    raw = _cell(ws, mm, row, 2)
    # Check if this is part of a known section type
    for st in SECTION_TYPES:
        if st in raw or raw in st:
            return st
    # Single char might be part of '评分项'
    if raw in ('评', '分', '项'):
        return '评分项'
    return raw


def _get_indicator(ws, mm, row):
    """Get indicator label (I/安全/II/耐久/...) from col D, merge-aware."""
    return _cell(ws, mm, row, 4)


def _get_table_row(ws, mm, row):
    """提取 col N(14) O(15) P(16) Q(17) 拼成一行文本（表格数据）。"""
    cols = [_cell(ws, mm, row, c) for c in (14, 15, 16, 17)]
    # 去掉与 col N 重复的合并值（col O/P/Q 可能因合并与 N 相同）
    parts = [cols[0]]
    for c in cols[1:]:
        if c and c != cols[0]:
            parts.append(c)
    text = ' '.join(p for p in parts if p)
    return text if text != cols[0] else cols[0]  # 如果只有 N 有内容就返回 N


def _get_clauses(ws, mm, start_row, end_row):
    """Get clauses grouped by main clause number.

    A main clause starts with pattern like '4.1.1', '4.2.8' in col E.
    Sub-items ('1 ...', '2 ...', '1) ...') are merged into the parent clause.
    Also tracks section type (控制项/评分项/加分项) from col B,
    and indicator label (I安全/II耐久/...) from col D per row.
    """
    import re
    MAIN_RE = re.compile(r'^\d+\.\d+\.\d+')
    ROMAN_RE = re.compile('^[IVX]+$')

    clauses = []
    seen_e_roots = set()
    current_clause = None
    prev_sec = None
    prev_indicator = ''      # 累积罗马+中文: "I安全", "II耐久"
    pending_roman = ''       # 暂存的罗马数字

    for ri in range(start_row, end_row + 1):
        # Track section type from col B
        raw_b = _cell(ws, mm, ri, 2)
        if raw_b:
            prev_sec = _get_section_type(ws, mm, ri)

        # Track indicator from col D (罗马数字 + 中文名)
        raw_d = _cell(ws, mm, ri, 4)
        if raw_d:
            if ROMAN_RE.match(raw_d):
                pending_roman = raw_d
            elif raw_d in ('控制项', '评分项', '加分项'):
                # 切换 section type 时 reset indicator
                if raw_d == '控制项':
                    prev_indicator = raw_d
                else:
                    prev_indicator = ''
                pending_roman = ''
            else:
                # 中文名: 拼接罗马数字
                prev_indicator = pending_roman + raw_d
                pending_roman = ''

        e_root = mm.get((ri, 5), (ri, 5))
        if e_root in seen_e_roots:
            continue
        seen_e_roots.add(e_root)

        e_val = _cell(ws, mm, ri, 5)
        if not e_val:
            continue

        is_main = bool(MAIN_RE.match(e_val))

        if is_main:
            l_val = _cell(ws, mm, ri, 12)
            m_val = _cell(ws, mm, ri, 13)
            n_val = _get_table_row(ws, mm, ri)
            current_clause = {
                'clause': e_val,
                'score_max': l_val,
                'score': m_val,
                'measures': n_val,
                'type_label': prev_sec or '',
                'indicator': prev_indicator,
            }
            clauses.append(current_clause)
        else:
            if current_clause is None:
                continue
            n_val = _get_table_row(ws, mm, ri)
            if n_val:
                if current_clause['measures']:
                    current_clause['measures'] += '\n' + n_val
                else:
                    current_clause['measures'] = n_val

    return clauses


def extract_xlsx(xlsx_path):
    """Extract ALL clauses as a flat list from xlsx (按顺序，不分 group)。

    Returns list of dicts, each with:
      clause, score_max, score, measures, cat_label, type_label
    """
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb['Sheet1']
    mm = _build_merge_map(ws)

    chapters = _find_chapters(ws, mm)
    print(f"  Chapters: {[(c['num'], c['row']) for c in chapters]}")

    all_clauses = []
    for ci, ch in enumerate(chapters):
        end_row = chapters[ci + 1]['row'] - 1 if ci + 1 < len(chapters) else ws.max_row
        start_row = ch['row']

        # Chapter title from col A
        title_parts = []
        for ri in range(start_row, min(start_row + 5, end_row + 1)):
            v = _cell(ws, mm, ri, 1)
            if v:
                title_parts.append(v)
        ch_title = ''.join(title_parts)

        # Extract clauses (type_label tracked inside _get_clauses)
        clauses = _get_clauses(ws, mm, start_row, end_row)
        for cl in clauses:
            cl['cat_label'] = ch_title
            all_clauses.append(cl)

        print(f"  Ch {ch['num']} ({ch_title}): {len(clauses)} clauses")

    print(f"  Total: {len(all_clauses)} clauses")
    return all_clauses


# ── LISP 生成 ────────────────────────────────────────────────

# 表格列定义: 评价指标类别 | 指标类型 | 规范条文 | 满分 | 评分 | 技术措施
TABLE_COLS = 7
HEADER = ['评价指标类别', '指标类型', '', '规范条文', '满分', '评分', '技术措施']

# 列宽 (x100): 类别/类型/指标 窄, 满分/评分 窄, 条文和措施宽
def _col_widths(total_w):
    w_cat = 1200       # 评价指标类别 12mm
    w_type = 1200      # 指标类型 12mm
    w_ind = 1000       # 指标 10mm
    w_score = 800      # 满分/评分 8mm
    remaining = total_w - w_cat - w_type - w_ind - w_score * 2
    return [
        w_cat,                      # 评价指标类别
        w_type,                     # 指标类型
        w_ind,                      # 指标
        int(remaining * 0.47),      # 规范条文
        w_score,                    # 满分
        w_score,                    # 评分
        int(remaining * 0.53),      # 技术措施
    ]

ROW_HDR_H = 1500   # 表头行高 15mm × 100
ROW_DATA_H = 800    # 最小数据行高 8mm × 100 (AutoCAD 自动撑开)
MAX_ROW_H = 12000   # 单行最大高度 120mm
FONT_H = 300        # 字高 3mm × 100
LINE_H = 400        # 每行文字高度估算 (含行距)


def _calc_row_h(row, col_ws):
    """根据单元格内容计算行高，限制最大值。"""
    max_lines = 1
    for ci, cell in enumerate(row):
        if not cell:
            continue
        cw = col_ws[ci] if ci < len(col_ws) else 5000
        chars_per_line = max(cw // FONT_H, 1)
        explicit_lines = cell.count('\n') + 1
        wrap_lines = max(len(cell) // chars_per_line, 1)
        max_lines = max(max_lines, max(explicit_lines, wrap_lines))
    return min(MAX_ROW_H, max(ROW_DATA_H, LINE_H * max_lines))


def _make_table_lisp(L, header_row, data_rows, row_heights, insert_x, insert_y, col_ws):
    """Generate LISP to create one ACAD_TABLE.

    Row 0 = Title (height=1)
    Row 1 = Header
    Row 2+ = Data (heights from row_heights list)
    """
    nr = 2 + len(data_rows)
    nc = len(header_row)
    a = L.append

    default_w = max(col_ws)
    a(f'  (setq pt (vlax-3d-point {insert_x} {insert_y}))')
    a(f'  (setq tbl (vla-AddTable msp pt {nr} {nc} {ROW_HDR_H} {default_w}))')
    a('  (if tbl (princ "\\n[xlsx2dwg] Table OK") (princ "\\n[xlsx2dwg] Table FAILED"))')
    # 应用表格样式（含宋体）
    a('  (vla-put-StyleName tbl "SongTiTable")')
    a('  (vla-put-TitleSuppressed tbl :vlax-true)')
    a(f'  (vla-put-VertCellMargin tbl {FONT_H // 2})')

    for ci, w in enumerate(col_ws):
        a(f'  (vla-SetColumnWidth tbl {ci} {w})')

    a('  (vla-SetRowHeight tbl 0 1)')
    a(f'  (vla-SetRowHeight tbl 1 {ROW_HDR_H})')
    for di, rh in enumerate(row_heights):
        a(f'  (vla-SetRowHeight tbl {di + 2} {rh})')

    for ci, cell in enumerate(header_row):
        txt = esc(cell)
        a(f'  (vla-SetText tbl 1 {ci} "{txt}")')
        a(f'  (vla-SetCellTextHeight tbl 1 {ci} {FONT_H})')
        a(f'  (vla-SetCellAlignment tbl 1 {ci} 5)')
    # 表头: 合并 col 1+2 为 "指标类型"
    a('  (vla-MergeCells tbl 1 1 1 2)')

    for di, row in enumerate(data_rows):
        ri = di + 2
        for ci, cell in enumerate(row):
            if not cell:
                continue
            txt = esc(cell)
            a(f'  (vla-SetText tbl {ri} {ci} "{txt}")')
            a(f'  (vla-SetCellTextHeight tbl {ri} {ci} {FONT_H})')
            a(f'  (vla-SetCellAlignment tbl {ri} {ci} 1)')

    # 合并单元格
    a('  (vla-put-RegenerateTableSuppressed tbl :vlax-true)')

    # col 0 (评价指标类别): 上下相同合并
    start = 0
    while start < len(data_rows):
        val = data_rows[start][0] if len(data_rows[start]) > 0 else ''
        end = start
        while end + 1 < len(data_rows):
            next_val = data_rows[end + 1][0] if len(data_rows[end + 1]) > 0 else ''
            if next_val == val and val:
                end += 1
            else:
                break
        if end > start:
            r1, r2 = start + 2, end + 2
            a(f'  (vla-MergeCells tbl {r1} {r2} 0 0)')
        start = end + 1

    # col 1+2: 按 type_label 分组合并
    start = 0
    while start < len(data_rows):
        type_val = data_rows[start][1] if len(data_rows[start]) > 1 else ''
        if not type_val:
            start += 1
            continue
        end = start
        while end + 1 < len(data_rows):
            next_type = data_rows[end + 1][1] if len(data_rows[end + 1]) > 1 else ''
            if next_type == type_val:
                end += 1
            else:
                break
        r1, r2 = start + 2, end + 2
        if type_val == '控制项':
            # 控制项: col 1+2 纵向+横向同时合并
            a(f'  (vla-MergeCells tbl {r1} {r2} 1 2)')
        else:
            # 评分项/加分项: 只合并 col 1 纵向
            if end > start:
                a(f'  (vla-MergeCells tbl {r1} {r2} 1 1)')
        start = end + 1

    # col 2 (指标): 评分项上下相同合并
    start = 0
    while start < len(data_rows):
        type_val = data_rows[start][1] if len(data_rows[start]) > 1 else ''
        ind_val = data_rows[start][2] if len(data_rows[start]) > 2 else ''
        if type_val != '评分项' or not ind_val:
            start += 1
            continue
        end = start
        while end + 1 < len(data_rows):
            next_type = data_rows[end + 1][1] if len(data_rows[end + 1]) > 1 else ''
            next_ind = data_rows[end + 1][2] if len(data_rows[end + 1]) > 2 else ''
            if next_type == '评分项' and next_ind == ind_val:
                end += 1
            else:
                break
        if end > start:
            r1, r2 = start + 2, end + 2
            a(f'  (vla-MergeCells tbl {r1} {r2} 2 2)')
        start = end + 1

    a('  (vla-put-RegenerateTableSuppressed tbl :vlax-false)')


def generate_lisp(clauses, output_dwg, max_clauses=None):
    """Generate LISP: flat clause list → 3 columns greedy packing.

    逐条算行高，往当前列塞，满了换下一列。
    """
    L = []
    a = L.append

    a('(vl-load-com)')
    a('(defun c:Xlsx2Dwg (/ acad doc msp tbl pt tx ts dict tsname)')
    a('  (setq acad (vlax-get-acad-object))')
    a('  (setq doc (vla-get-ActiveDocument acad))')
    a('  (setq msp (vla-get-ModelSpace doc))')
    a('  (setvar "FILEDIA" 0)')
    a('  (setvar "CMDECHO" 0)')
    # 创建文字样式: vla-setFont 确保宋体应用到单元格
    a('  (setq ts (vla-Add (vla-get-TextStyles doc) "lvjianst"))')
    a('  (vla-setFont ts "宋体" :vlax-false :vlax-false 134 2)')
    # 创建表格样式 AcDbTableStyle，引用 lvjianst
    a('  (setq dict (vla-Item (vla-get-Dictionaries doc) "acad_tablestyle"))')
    a('  (setq tsname (vla-AddObject dict "SongTiTable" "AcDbTableStyle"))')
    a('  (vla-SetTextStyle tsname 1 "lvjianst")')
    a('  (vla-SetTextStyle tsname 2 "lvjianst")')
    a('  (vla-SetTextHeight tsname 1 300)')
    a('  (vla-SetTextHeight tsname 2 300)')

    col_ws = _col_widths(COL_W)
    avail_h = TABLE_TOP - DRAW_BOTTOM  # 每列可用高度
    data_area_h = avail_h - 1 - ROW_HDR_H  # 减去 title(1) + header(1500)

    # 构建 flat rows: 控制项时 col 1+2 合并显示, col 2 留空
    all_rows = []
    for cl in clauses:
        ind = cl.get('indicator', '')
        type_val = cl.get('type_label', '')
        if type_val == '控制项':
            # 控制项: col 1="控制项", col 2=""(合并后只显示col1)
            ind = ''
        all_rows.append([
            cl.get('cat_label', ''),
            type_val,
            ind,
            cl['clause'],
            cl['score_max'],
            cl['score'],
            cl['measures'],
        ])

    if max_clauses:
        all_rows = all_rows[:max_clauses]

    # 贪婪装箱：3 列
    col_data = [[] for _ in range(COL_COUNT)]   # 每列的 rows
    col_rhs = [[] for _ in range(COL_COUNT)]     # 每列的行高
    col_h = [0] * COL_COUNT                      # 每列累计高度
    col_idx = 0

    for row in all_rows:
        rh = _calc_row_h(row, col_ws)
        # 当前列还能放吗？
        if col_h[col_idx] + rh <= data_area_h:
            col_data[col_idx].append(row)
            col_rhs[col_idx].append(rh)
            col_h[col_idx] += rh
        else:
            # 换下一列
            col_idx += 1
            if col_idx >= COL_COUNT:
                break  # 3 列满了，剩下的不要了
            col_data[col_idx].append(row)
            col_rhs[col_idx].append(rh)
            col_h[col_idx] += rh

    # 生成 LISP
    table_count = 0
    for ci in range(COL_COUNT):
        if not col_data[ci]:
            continue
        x = DRAW_LEFT + ci * (COL_W + COL_GAP)
        y = TABLE_TOP
        _make_table_lisp(L, HEADER, col_data[ci], col_rhs[ci], x, y, col_ws)
        table_count += 1

    # Save DWG 2007
    dwg_fwd = output_dwg.replace('\\', '/')
    a(f'  (setvar "FILEDIA" 0)')
    a(f'  (command "_.SAVEAS" "2007" "{dwg_fwd}")')
    a('  (princ "\\n[xlsx2dwg] Done.")')
    a(')')

    total = sum(len(d) for d in col_data)
    for ci in range(COL_COUNT):
        print(f"  Col {ci}: {len(col_data[ci])} rows, h={col_h[ci]} ({col_h[ci]/100:.0f}mm)")
    print(f"  Total clauses packed: {total}/{len(all_rows)}, {table_count} tables, {len(L)} LISP lines")
    return '\n'.join(L)


# ── 转换流程 ─────────────────────────────────────────────────

def convert(xlsx_path, output_dir=None, max_clauses=None):
    """Convert xlsx scoring table to DWG via acad.exe."""
    output_dir = output_dir or str(Path(xlsx_path).parent / "_dwg_test")
    os.makedirs(output_dir, exist_ok=True)

    dwg_out = os.path.abspath(os.path.join(output_dir, "xlsx_output.dwg"))
    if os.path.isfile(dwg_out):
        os.remove(dwg_out)

    # 提取扁平条款列表
    clauses = extract_xlsx(xlsx_path)

    lisp_content = generate_lisp(clauses, dwg_out, max_clauses=max_clauses)
    lsp_file = os.path.abspath(os.path.join(output_dir, "_xlsx2dwg.lsp"))
    scr_file = os.path.abspath(os.path.join(output_dir, "_xlsx2dwg.scr"))

    with open(lsp_file, 'w', encoding='gbk', errors='replace') as f:
        f.write(lisp_content)

    lsp_fwd = lsp_file.replace('\\', '/')
    with open(scr_file, 'w', encoding='utf-8') as f:
        f.write('(setvar "FILEDIA" 0)\n')
        f.write('(setvar "CMDECHO" 0)\n')
        f.write(f'(load "{lsp_fwd}")\n')
        f.write('(c:Xlsx2Dwg)\n')
        f.write('(command "_.QUIT" "Y")\n')

    print(f"[1/2] LISP ({len(lisp_content):,} chars) + SCR generated")

    # 一次 acad.exe：开 A1.dwt → 执行 LISP 创建表格 → SAVEAS 输出
    dwt_path = A1_TEMPLATE.replace('\\', '/')
    cmd = f'"{ACAD_EXE}" "{dwt_path}" /b "{scr_file}"'
    print(f"[2/2] Running acad.exe (A1.dwt + LISP)...")
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180, cwd=output_dir)
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
        r"C:\opt\ACADxPDF\.test\安全与耐久性评价表.xlsx"
    )
    max_clauses = int(os.environ.get("MAX_CLAUSES", "0")) or None
    convert(f, max_clauses=max_clauses)
