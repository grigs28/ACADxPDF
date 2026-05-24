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
import os, sys, io, json, hashlib, re, math
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import openpyxl
from openpyxl.utils import get_column_letter

# ── 布局参数 ──
SCALE = 100
FONT_H = 350
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
COL_W = (DRAW_W - COL_GAP * (COL_COUNT - 1)) // COL_COUNT  # ~23867

CN_NUMS = ['一','二','三','四','五','六','七','八','九','十',
           '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十']


def _calc_table_height(row_heights):
    """表格高度 = 行高之和"""
    return sum(row_heights) if row_heights else 0


def _est_line_width(text, font_h):
    """估算文本渲染宽度：CJK 字符 ≈ font_h，ASCII ≈ font_h*0.5"""
    w = 0.0
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F or
                0xFF00 <= cp <= 0xFFEF or 0x3400 <= cp <= 0x4DBF or
                0xF900 <= cp <= 0xFAFF):
            w += font_h
        else:
            w += font_h * 0.5
    return w


def _detect_heading_level(text):
    """检测标题层级 → (level, text_height, bold)"""
    if re.match(r'^[一二三四五六七八九十]+、', text):
        return (1, 450, True)
    if re.match(r'^[（(][一二三四五六七八九十]+[)）]', text):
        return (2, 400, True)
    return (0, 350, False)


def _calc_row_height(text, width, font_h):
    """单行文本在无边框 Table 中的行高。"""
    if not text:
        return font_h * 1.5
    line_w = _est_line_width(text, font_h)
    num_wrap = max(1, math.ceil(line_w / width))
    return math.ceil(num_wrap * font_h * 1.5)


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
                    if v:
                        cells.append({
                            'row': ri - s, 'col': ci,
                            'text': v, 'text_height': 0.0, 'alignment': 5,
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

            raw_sec_list.append({
                'type': 'table',
                'nrows': nrows_sec, 'ncols': actual_ncols,
                'col_widths': sec_col_ws,
                'row_heights': row_hs_trimmed,
                'cells': cells, 'merges': merges,
                'text_height': FONT_H,
                'col_a_root': cell(s, 0)[:30],
            })
        else:
            # Text section → 1-column borderless table
            cells = []
            row_heights = []
            for ri in range(s, e + 1):
                v = cell(ri, 1)  # col B via merge lookup
                if not v:
                    continue
                level, th, bold = _detect_heading_level(v)
                cells.append({
                    'row': len(row_heights), 'col': 0,
                    'text': v, 'text_height': th, 'alignment': 4,
                    'bold': bold,
                })
                row_heights.append(_calc_row_height(v, COL_W, th))
            if not cells:
                continue
            raw_sec_list.append({
                'type': 'table',
                'borderless': True,
                'nrows': len(row_heights), 'ncols': 1,
                'col_widths': [COL_W],
                'row_heights': row_heights,
                'cells': cells, 'merges': [],
                'text_height': FONT_H,
                'col_a_root': cell(s, 0)[:30],
            })

    return {
        'raw_sections': raw_sec_list,
        'col_ws': col_ws,
        'ncols': ncols,
        'font': {'name': '宋体', 'charset': 134, 'pitch_and_family': 2},
    }


def _split_table_section(sec, max_h):
    """将一个大表格拆分为多个 chunk，每个 chunk 高度 ≤ max_h。

    返回 list[dict]，每个 dict 是一个独立的 TABLE section。
    支持 row_borderless 列表同步切割。
    """
    row_hs = sec['row_heights']
    nrows = sec['nrows']
    ncols = sec['ncols']
    cells = sec['cells']
    merges = sec['merges']
    row_bl = sec.get('row_borderless', [])

    chunks = []
    chunk_start = 0
    chunk_h = 0

    for ri in range(nrows):
        rh = row_hs[ri] if ri < len(row_hs) else 500
        if chunk_h + rh > max_h and ri > chunk_start:
            chunks.append((chunk_start, ri - 1))
            chunk_start = ri
            chunk_h = rh
        else:
            chunk_h += rh
    chunks.append((chunk_start, nrows - 1))

    if len(chunks) <= 1:
        return [sec]

    result = []
    for (rs, re) in chunks:
        cnr = re - rs + 1
        c_cells = [{**c} for c in cells if rs <= c['row'] <= re]
        for c in c_cells:
            c['row'] -= rs

        c_merges = []
        for m in merges:
            if m['r1'] > re or m['r2'] < rs:
                continue
            lr1 = max(m['r1'] - rs, 0)
            lr2 = min(m['r2'] - rs, cnr - 1)
            if lr1 <= lr2:
                c_merges.append({'r1': lr1, 'c1': m['c1'], 'r2': lr2, 'c2': m['c2']})

        c_rhs = row_hs[rs:re + 1]
        c_bl = row_bl[rs:re + 1] if row_bl else []

        sec_out = {
            'type': 'table',
            'nrows': cnr, 'ncols': ncols,
            'col_widths': sec['col_widths'],
            'row_heights': c_rhs,
            'cells': c_cells, 'merges': c_merges,
            'text_height': sec.get('text_height', FONT_H),
        }
        if c_bl:
            sec_out['row_borderless'] = c_bl
        result.append(sec_out)

    return result


def build_mega_table(analysis):
    """将所有 raw sections 合并为一个 mega-table。

    文本段行：1 cell at col 0，全列合并，无边框。
    数据表行：cells 直接搬入，有边框。
    """
    raw_sections = analysis['raw_sections']
    if not raw_sections:
        return None

    col_ws_orig = analysis['col_ws']

    # 确定全局 max ncols（从有边框数据表）
    max_ncols = 1
    for sec in raw_sections:
        if not sec.get('borderless', False):
            max_ncols = max(max_ncols, sec.get('ncols', 1))

    # col_widths：用原始前 max_ncols 列，缩放到填满 COL_W
    cw = list(col_ws_orig[:max_ncols])
    total_cw = sum(cw)
    if total_cw > 0:
        scale = COL_W / total_cw
        cw = [round(w * scale, 1) for w in cw]

    all_cells = []
    all_merges = []
    all_row_heights = []
    all_row_borderless = []
    row_offset = 0

    for sec in raw_sections:
        is_borderless = sec.get('borderless', False)
        sec_nrows = sec.get('nrows', 0)

        if is_borderless:
            # 文本段：每行 → 1 cell at col 0，全列合并
            cell_idx = 0
            for ri in range(sec_nrows):
                # 查找对应的 cell
                matching = [c for c in sec['cells'] if c['row'] == ri]
                if not matching:
                    # 空行
                    all_row_heights.append(sec['row_heights'][ri] if ri < len(sec['row_heights']) else FONT_H * 1.5)
                    all_row_borderless.append(True)
                    continue

                c = matching[0]
                all_row_heights.append(sec['row_heights'][ri] if ri < len(sec['row_heights']) else FONT_H * 1.5)
                all_row_borderless.append(True)

                new_row = row_offset + ri
                all_cells.append({
                    'row': new_row, 'col': 0,
                    'text': c['text'],
                    'text_height': c.get('text_height', 0),
                    'alignment': c.get('alignment', 4),
                    'bold': c.get('bold', False),
                })
                # 全列合并
                all_merges.append({'r1': new_row, 'c1': 0, 'r2': new_row, 'c2': max_ncols - 1})

            row_offset += sec_nrows
        else:
            # 数据表：cells 直接搬入，row 加偏移
            for c in sec['cells']:
                all_cells.append({
                    **c,
                    'row': c['row'] + row_offset,
                })
            for m in sec['merges']:
                all_merges.append({
                    'r1': m['r1'] + row_offset,
                    'c1': m['c1'],
                    'r2': m['r2'] + row_offset,
                    'c2': m['c2'],
                })

            for ri in range(sec_nrows):
                rh = sec['row_heights'][ri] if ri < len(sec['row_heights']) else 500
                all_row_heights.append(rh)
                all_row_borderless.append(False)

            row_offset += sec_nrows

    return {
        'type': 'table',
        'nrows': len(all_row_heights),
        'ncols': max_ncols,
        'col_widths': cw,
        'row_heights': all_row_heights,
        'cells': all_cells,
        'merges': all_merges,
        'text_height': FONT_H,
        'row_borderless': all_row_borderless,
    }


def layout_pages(analysis):
    """将 raw sections 合并为 mega-table，切分后布局到 A1 多图。"""
    mega = build_mega_table(analysis)
    if not mega or mega['nrows'] == 0:
        return [], 0

    max_col_h = CONTENT_H - 2000  # 52400 - 2000 = 50400
    chunks = _split_table_section(mega, max_col_h)

    # 贪心装箱：3 列/页
    result_sections = []
    page_index = 0
    col_idx = 0
    col_y = CONTENT_TOP + 2000

    for chunk in chunks:
        sec_h = _calc_table_height(chunk['row_heights'])
        sec_h = max(sec_h, FONT_H)

        remaining = col_y - DRAW_BOTTOM
        if sec_h > remaining:
            col_idx += 1
            if col_idx >= COL_COUNT:
                page_index += 1
                col_idx = 0
            col_y = CONTENT_TOP + 2000

        page_x = page_index * (FRAME_W + FRAME_GAP)
        col_x = DRAW_LEFT + col_idx * (COL_W + COL_GAP)
        insert_x = page_x + col_x
        insert_y = col_y

        sec_out = {
            'type': 'table',
            'insert': [round(insert_x, 0), round(insert_y, 0), 0.0],
            'nrows': chunk['nrows'],
            'ncols': chunk['ncols'],
            'col_widths': chunk['col_widths'],
            'row_heights': chunk['row_heights'],
            'default_text_height': chunk.get('text_height', FONT_H),
            'title_suppressed': True,
            'cells': chunk['cells'],
            'merges': chunk['merges'],
        }
        rb = chunk.get('row_borderless', [])
        if rb:
            sec_out['row_borderless'] = rb
        result_sections.append(sec_out)

        col_y -= sec_h

    # 添加每页标题（MText 居中）
    num_pages = page_index + 1
    for pi in range(num_pages):
        px = pi * (FRAME_W + FRAME_GAP)
        cn = CN_NUMS[pi] if pi < len(CN_NUMS) else str(pi + 1)
        title_x = px + DRAW_LEFT + DRAW_W / 2
        result_sections.append({
            'type': 'mtext',
            'text': f'绿色建筑设计专篇（建筑）{cn}',
            'insert': [round(title_x, 0), round(DRAW_TOP - 1000, 0), 0.0],
            'width': round(DRAW_W, 0),
            'text_height': 600,
            'bold': True,
            'attachment': 'TopCenter',
        })

    return result_sections, num_pages


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
    n_borderless = sum(1 for s in raw if s.get('borderless'))
    n_table = sum(1 for s in raw if s['type'] == 'table' and not s.get('borderless'))
    print(f"  Raw: {n_borderless} text(borderless), {n_table} table")

    print("Layout...")
    sections, num_pages = layout_pages(analysis)
    print(f"  Pages: {num_pages}, sections: {len(sections)}")

    for sec in sections:
        if sec['type'] == 'mtext':
            print(f"    PAGE TITLE: {sec['text']}")
        else:
            bl = ' BORDERLESS' if sec.get('borderless') else ''
            print(f"    TABLE{bl}: ({sec['insert'][0]:.0f},{sec['insert'][1]:.0f}) "
                  f"{sec['nrows']}×{sec['ncols']} cells={len(sec['cells'])} merges={len(sec['merges'])}")

    doc = {
        'version': 1,
        'save_format': '2007',
        'font': analysis['font'],
        'default_text_height': FONT_H,
        'sections': sections,
        'num_pages': num_pages,
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
