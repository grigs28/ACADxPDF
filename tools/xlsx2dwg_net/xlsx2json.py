#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xlsx2json.py — xlsx → JSON 转换器（A1 多图布局）

按 xlsx2dwg-design-spec.md 规格：
- A1 图框 84100×59400，绘图区 (2500,1000)~(77100,58400)
- 3 列布局，标题区 5000 高
- 3 列满→新图，标题递增 一/二/三...
- 大表格自动拆分为适合列高的 chunk
- 同一条款组不拆分

Usage:
    python xlsx2json.py [xlsx] [output_dir] [sheet_name] [max_rows]
"""
import os, sys, io, json, hashlib, re
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import openpyxl
from openpyxl.utils import get_column_letter

# ── 布局参数 ──
SCALE = 100
FONT_H = 300
ROW_PT_MM = 0.353

# A1 图框 (1:100)
FRAME_W = 84100
FRAME_H = 59400
FRAME_GAP = 2000
DRAW_LEFT = 2500
DRAW_BOTTOM = 1000
DRAW_RIGHT = 77100
DRAW_TOP = 58400
DRAW_W = DRAW_RIGHT - DRAW_LEFT    # 74600
DRAW_H = DRAW_TOP - DRAW_BOTTOM    # 57400

TITLE_AREA_H = 5000
CONTENT_TOP = DRAW_TOP - TITLE_AREA_H  # 53400
CONTENT_H = CONTENT_TOP - DRAW_BOTTOM  # 52400

COL_COUNT = 3
COL_GAP = 1500
COL_LEFT_PAD = -500   # 第一列右移500
COL_RIGHT_PAD = -500  # 第三列左移500
COL_W = (DRAW_W + COL_LEFT_PAD + COL_RIGHT_PAD - COL_GAP * (COL_COUNT - 1)) // COL_COUNT  # ~23567

CN_NUMS = ['一','二','三','四','五','六','七','八','九','十',
           '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十']


def _calc_table_height(row_heights):
    """表格高度 = 行高之和"""
    return sum(row_heights) if row_heights else 0


def _detect_heading_level(text):
    """检测标题层级 → (level, text_height, bold)"""
    if re.match(r'^[一二三四五六七八九十]+、', text):
        return (1, 320, True)
    if re.match(r'^[（(][一二三四五六七八九十]+[)）]', text):
        return (2, 310, True)
    return (0, 300, False)


def _detect_header_nrows(merges, nrows):
    """检测表格段的表头行数。启发式：找起始处跨多列且跨行(1-2行)的合并范围。"""
    max_r2 = 0
    for m in merges:
        r1, c1, r2, c2 = m['r1'], m['c1'], m['r2'], m['c2']
        if r1 <= 1 and (c2 - c1) >= 1 and 1 <= (r2 - r1) <= 2:
            max_r2 = max(max_r2, r2)
    return max(max_r2 + 1, 1)


def _read_cell_border(ws, ri, ci):
    """读取单元格边框可见性 → 字符串如 'TBLR', 'B', 'TBR' 等"""
    c = ws.cell(ri + 1, ci + 1)
    parts = []
    if c.border.top and c.border.top.style and c.border.top.style != 'none':
        parts.append('T')
    if c.border.bottom and c.border.bottom.style and c.border.bottom.style != 'none':
        parts.append('B')
    if c.border.left and c.border.left.style and c.border.left.style != 'none':
        parts.append('L')
    if c.border.right and c.border.right.style and c.border.right.style != 'none':
        parts.append('R')
    return ''.join(parts)


def analyze_sheet(ws, max_rows=None):
    """分析 xlsx sheet → raw sections (无坐标，只有数据和尺寸)"""
    nrows = ws.max_row or 1
    ncols = ws.max_column or 1
    if max_rows:
        nrows = min(nrows, max_rows)

    # 合并映射
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

    # 检测边框
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

    # 分段
    raw_sections = []
    if nrows > 0:
        cur_type = 'mtext' if not row_has_border[0] else 'table'
        cur_start = 0
        for ri in range(1, nrows):
            rtype = 'mtext' if not row_has_border[ri] else 'table'
            if rtype != cur_type:
                raw_sections.append({'type': cur_type, 'start': cur_start, 'end': ri - 1})
                cur_type = rtype
                cur_start = ri
        raw_sections.append({'type': cur_type, 'start': cur_start, 'end': nrows - 1})

    # 列宽
    col_widths_xlsx = []
    for ci in range(ncols):
        dim = ws.column_dimensions[get_column_letter(ci + 1)]
        col_widths_xlsx.append(dim.width if dim.width else 8.43)

    # 计算列宽（适配 A1 列宽）
    # 原始 19 列 × 2100 ≈ 39900，远大于 COL_W≈23867
    # 缩放到列宽内
    raw_total_w = sum(w * 2100 for w in col_widths_xlsx)
    scale_factor = COL_W / raw_total_w if raw_total_w > 0 else 1.0
    col_ws = [round(w * 2100 * scale_factor, 1) for w in col_widths_xlsx]

    # 构建原始节段
    raw_sec_list = []
    for sec in raw_sections:
        s, e = sec['start'], sec['end']
        nrows_sec = e - s + 1

        if sec['type'] == 'table':
            cells = []
            for ri in range(s, e + 1):
                for ci in range(ncols):
                    v = cell(ri, ci)
                    brd = _read_cell_border(ws, ri, ci)
                    if v or brd:
                        cells.append({
                            'row': ri - s, 'col': ci,
                            'text': v, 'text_height': 0.0, 'alignment': 5,
                            'borders': brd,
                        })

            merges = []
            for (mr, mc, er, ec) in merge_ranges:
                if mr > e or er < s:
                    continue
                local_mr = max(mr - s, 0)
                local_er = min(er - s, e - s)
                if local_mr <= local_er:
                    merges.append({'r1': local_mr, 'c1': mc, 'r2': local_er, 'c2': ec})

            row_hs = []
            for ri in range(s, e + 1):
                dim = ws.row_dimensions[ri + 1]
                pt = dim.height if dim.height else 15
                row_hs.append(round(pt * ROW_PT_MM * SCALE, 1))

            # 裁剪尾部空列
            actual_ncols = ncols
            has_content_cols = set(c['col'] for c in cells if c['text'].strip())
            if has_content_cols:
                max_content_col = max(has_content_cols)
                if max_content_col < ncols - 1:
                    actual_ncols = max_content_col + 1
                    cells = [c for c in cells if c['col'] < actual_ncols]
                    merges = [m for m in merges if m['c1'] < actual_ncols]
                    for m in merges:
                        m['c2'] = min(m['c2'], actual_ncols - 1)
                    trimmed_w = sum(col_ws[actual_ncols:])
                    sec_col_ws = list(col_ws[:actual_ncols])
                    # 均摊被裁剪列的宽度到剩余列
                    if trimmed_w > 0 and len(sec_col_ws) > 0:
                        extra = trimmed_w / len(sec_col_ws)
                        sec_col_ws = [round(w + extra, 1) for w in sec_col_ws]
                    row_hs_trimmed = row_hs
                else:
                    sec_col_ws = list(col_ws)
                    row_hs_trimmed = row_hs
            else:
                sec_col_ws = list(col_ws)
                row_hs_trimmed = row_hs

            header_nrows = _detect_header_nrows(merges, nrows_sec)

            raw_sec_list.append({
                'type': 'table',
                'nrows': nrows_sec, 'ncols': actual_ncols,
                'col_widths': sec_col_ws,
                'row_heights': row_hs_trimmed,
                'cells': cells, 'merges': merges,
                'text_height': FONT_H,
                'header_nrows': header_nrows,
                'col_a_root': cell(s, 0)[:30],
            })
        else:
            # Text section → detect dual-column (left col 0-8, right col 9+)
            # 检查数据行的 col 9 是否有独立内容（排除被左列全行合并覆盖的情况）
            has_right = False
            for ri in range(s, e + 1):
                raw_v = ws.cell(ri + 1, 10).value  # col J 原始值，不经合并查找
                if raw_v and str(raw_v).strip():
                    has_right = True
                    break

            cells = []
            row_heights = []
            merges = []
            text_ncols = 2 if has_right else 1

            for ri in range(s, e + 1):
                lv = cell(ri, 0)  # col A via merge lookup
                rv = ws.cell(ri + 1, 10).value if has_right else ''  # col J 原始值
                if rv:
                    rv = str(rv).strip()

                # 标题行（无右列内容，且左列有内容）
                if has_right and not rv and lv:
                    level, th, bold = _detect_heading_level(lv)
                    cells.append({
                        'row': len(row_heights), 'col': 0,
                        'text': lv, 'text_height': th, 'alignment': 4,
                        'bold': bold,
                    })
                    merges.append({'r1': len(row_heights), 'c1': 0,
                                   'r2': len(row_heights), 'c2': 1})
                    row_heights.append(0)
                elif has_right and (lv or rv):
                    # 双列数据行
                    if lv:
                        cells.append({
                            'row': len(row_heights), 'col': 0,
                            'text': lv, 'text_height': 0, 'alignment': 1,
                        })
                    if rv:
                        cells.append({
                            'row': len(row_heights), 'col': 1,
                            'text': rv, 'text_height': 0, 'alignment': 1,
                        })
                    row_heights.append(0)
                elif not has_right:
                    v = lv or cell(ri, 1)
                    if not v:
                        continue
                    level, th, bold = _detect_heading_level(v)
                    cells.append({
                        'row': len(row_heights), 'col': 0,
                        'text': v, 'text_height': th, 'alignment': 4,
                        'bold': bold,
                    })
                    row_heights.append(0)

            if not cells:
                continue
            raw_sec_list.append({
                'type': 'table',
                'borderless': True,
                'nrows': len(row_heights), 'ncols': text_ncols,
                'col_widths': [COL_W] if text_ncols == 1 else [COL_W / 2, COL_W / 2],
                'row_heights': row_heights,
                'cells': cells, 'merges': merges,
                'text_height': FONT_H,
                'col_a_root': cell(s, 0)[:30],
            })

    return {
        'raw_sections': raw_sec_list,
        'col_ws': col_ws,
        'ncols': ncols,
        'font': {'name': '宋体', 'charset': 134, 'pitch_and_family': 2},
    }





def extract_to_json(xlsx_path, output_dir=None, sheet_name=None, max_rows=None):
    """提取 xlsx → JSON 文件（A1 多图布局），返回 JSON 路径。"""
    output_dir = output_dir or str(Path(xlsx_path).parent)
    os.makedirs(output_dir, exist_ok=True)

    print(f"Reading: {xlsx_path}")
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    sn = sheet_name or wb.sheetnames[0]
    print(f"Sheet: {sn}")
    ws = wb[sn]

    print("Analyzing...")
    analysis = analyze_sheet(ws, max_rows=max_rows)

    raw = analysis['raw_sections']
    n_bl = sum(1 for s in raw if s.get('borderless'))
    n_tb = sum(1 for s in raw if s['type'] == 'table' and not s.get('borderless'))
    print(f"  Sections: {n_bl} text(borderless), {n_tb} table")

    doc = {
        'version': 2,
        'save_format': '2007',
        'font': analysis['font'],
        'default_text_height': FONT_H,
        'col_ws': analysis['col_ws'],
        'ncols': analysis['ncols'],
        'sections': raw,
        'title': sn,
    }

    stem = Path(xlsx_path).stem
    safe = hashlib.md5((stem + sn).encode()).hexdigest()[:8]
    json_name = f"{stem}_{sn}_{safe}.json"
    json_path = os.path.join(output_dir, json_name)

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    print(f"JSON: {json_path} ({os.path.getsize(json_path):,} bytes)")
    return json_path


if __name__ == "__main__":
    f = sys.argv[1] if len(sys.argv) > 1 else r"C:\opt\ACADxPDF\.test\design-doc-P038215-33264.xlsx"
    out = sys.argv[2] if len(sys.argv) > 2 else r"C:\opt\ACADxPDF\_test_output"
    sheet = sys.argv[3] if len(sys.argv) > 3 else "建筑"
    mr = int(sys.argv[4]) if len(sys.argv) > 4 else None
    extract_to_json(f, out, sheet, mr)
