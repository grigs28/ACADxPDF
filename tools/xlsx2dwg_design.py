#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xlsx2dwg_design.py — 绿色建筑设计专篇 xlsx → DWG

按xlsx原样复刻: 合并单元格、表格线(有边框=显示网格线/无边框=不显示)。
文本区域用MTEXT，表格区域用ACAD TABLE。
字高350，比例1:100。

Usage:
    python tools/xlsx2dwg_design.py [xlsx] [output_dir] [sheet_name]
"""
import os, sys, io, subprocess, time, re, hashlib
from pathlib import Path
from collections import Counter

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import openpyxl
from openpyxl.utils import get_column_letter

ACAD_EXE = os.environ.get("ACAD_EXE", r"C:\opt\AutoCAD 2026\acad.exe")
ACCCORE = os.environ.get("ACAD_PATH", r"C:\opt\AutoCAD 2026\accoreconsole.exe")
A1_TEMPLATE = r"C:\opt\ACADxPDF\Template\mt.dwt"

SCALE = 100
FONT_H = 350           # 字高 3.5mm × 100
COL_CHAR_MM = 2.54      # 1 xlsx char ≈ 2.54mm (8.75 chars × 2.54 ≈ 222mm per 19 cols? no...)
ROW_PT_MM = 0.353       # 1 pt ≈ 0.353mm

# ── LISP helpers ──
def esc(s):
    return (s.replace('\\', '\\\\').replace('"', '\\"')
             .replace('\n', '\\n').replace('\r', ''))

def esc_mtext(s):
    return (s.replace('\\', '\\\\').replace('"', '\\"')
             .replace('\n', '\\\\P').replace('\r', ''))


# ── xlsx analysis ──
def analyze_sheet(ws, max_rows=None):
    """Analyze xlsx sheet → sections list (MTEXT or TABLE)."""
    nrows = ws.max_row or 1
    ncols = ws.max_column or 1
    if max_rows:
        nrows = min(nrows, max_rows)

    # merge map
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

    # detect borders per row
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

    # classify rows into sections
    sections = []
    cur_type = 'text' if not row_has_border[0] else 'table'
    cur_start = 0

    for ri in range(1, nrows):
        rtype = 'text' if not row_has_border[ri] else 'table'
        if rtype != cur_type:
            sections.append({'type': cur_type, 'start': cur_start, 'end': ri - 1})
            cur_type = rtype
            cur_start = ri
    sections.append({'type': cur_type, 'start': cur_start, 'end': nrows - 1})

    # build cell data, merges per section
    col_widths_xlsx = []
    for ci in range(ncols):
        dim = ws.column_dimensions[get_column_letter(ci + 1)]
        col_widths_xlsx.append(dim.width if dim.width else 8.43)

    result_sections = []
    for sec in sections:
        s, e = sec['start'], sec['end']
        sec_data = {
            'type': sec['type'],
            'row_start': s,
            'row_end': e,
            'nrows': e - s + 1,
            'ncols': ncols,
        }

        if sec['type'] == 'table':
            # extract cell data (relative to section start)
            cells = {}
            for ri in range(s, e + 1):
                for ci in range(ncols):
                    v = cell(ri, ci)
                    if v:
                        cells[(ri - s, ci)] = v

            # extract merges (relative)
            merges = []
            for (mr, mc, er, ec) in merge_ranges:
                if mr > e or er < s:
                    continue
                local_mr = max(mr - s, 0)
                local_er = min(er - s, e - s)
                if local_mr <= local_er:
                    merges.append((local_mr, mc, local_er, ec))

            # row heights
            row_hs = []
            for ri in range(s, e + 1):
                dim = ws.row_dimensions[ri + 1]
                row_hs.append(dim.height if dim.height else 15)

            sec_data['cells'] = cells
            sec_data['merges'] = merges
            sec_data['row_heights_pt'] = row_hs

        else:  # text
            # collect text lines
            lines = []
            for ri in range(s, e + 1):
                v = cell(ri, 1)
                if v:
                    lines.append(v)
            sec_data['text'] = '\n'.join(lines)

        result_sections.append(sec_data)

    return {
        'nrows': nrows,
        'ncols': ncols,
        'col_widths_xlsx': col_widths_xlsx,
        'sections': result_sections,
    }


def generate_lisp(sheet_data, output_dwg):
    """Generate AutoLISP from analyzed sheet data."""
    L = []
    a = L.append
    ncols = sheet_data['ncols']
    col_ws_xlsx = sheet_data['col_widths_xlsx']

    # Column width in drawing units (1:100 scale)
    # Total width ≈ 400mm → 40000 at 1:100
    # Each col: 8.75 chars × ~2.4mm/char × 100 = ~2100
    COL_W_UNIT = 2100  # per xlsx column-width unit at 1:100
    col_ws = [w * COL_W_UNIT for w in col_ws_xlsx]

    a('(vl-load-com)')

    # Setup function
    a('(defun c:DsgnSetup (/ doc ts dict tsname)')
    a('  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))')
    a('  (setq ts (vl-catch-all-apply \'vla-Add (list (vla-get-TextStyles doc) "design_font")))')
    a('  (if (vl-catch-all-error-p ts)')
    a('    (setq ts (vla-Item (vla-get-TextStyles doc) "design_font")))')
    a('  (vla-setFont ts "宋体" :vlax-false :vlax-false 134 2)')
    a('  (setq dict (vla-Item (vla-get-Dictionaries doc) "acad_tablestyle"))')
    a('  (setq tsname (vl-catch-all-apply \'vla-AddObject (list dict "DsgnTblSt" "AcDbTableStyle")))')
    a('  (if (vl-catch-all-error-p tsname)')
    a('    (setq tsname (vla-Item dict "DsgnTblSt")))')
    a('  (vla-SetTextStyle tsname 1 "design_font")')
    a('  (vla-SetTextStyle tsname 2 "design_font")')
    a(f'  (vla-SetTextHeight tsname 1 {FONT_H})')
    a(f'  (vla-SetTextHeight tsname 2 {FONT_H})')
    a('  (princ "\\n[Setup] Done.")')
    a(')')

    y_offset = 0
    chunk_funcs = []
    CHUNK_ROWS = 60

    for sec_i, sec in enumerate(sheet_data['sections']):
        if sec['type'] == 'text':
            # MTEXT for borderless sections
            txt = sec.get('text', '')
            if not txt:
                continue
            fname = f"DsgnTxt{sec_i}"
            chunk_funcs.append(fname)

            mtext_h = (sec['nrows'] * 15 * ROW_PT_MM + 5) * SCALE  # estimate height
            insert_y = y_offset + mtext_h

            a(f'(defun c:{fname} (/ doc msp pt)')
            a('  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))')
            a('  (setq msp (vla-get-ModelSpace doc))')
            a(f'  (setq pt (vlax-3d-point 0 {insert_y:.0f} 0))')

            # Split into lines and create MTEXT
            lines = txt.split('\n')
            # Escape for MTEXT
            mtext_content = '\\P'.join(esc_mtext(ln) for ln in lines)
            a(f'  (vla-AddMText msp pt {int(sum(col_ws))} "{mtext_content}")')
            a(f'  (princ "\\n[{fname}] MTEXT {sec["nrows"]} rows")')
            a(')')
            y_offset -= mtext_h

        else:
            # TABLE for bordered sections - break into chunks
            nr = sec['nrows']
            cells = sec['cells']
            merges = sec['merges']
            row_hs_pt = sec['row_heights_pt']
            n_chunks = (nr + CHUNK_ROWS - 1) // CHUNK_ROWS

            for ci in range(n_chunks):
                r_start = ci * CHUNK_ROWS
                r_end = min(r_start + CHUNK_ROWS, nr)
                chunk_nr = r_end - r_start
                fname = f"DsgnTbl{sec_i}_{ci}"
                chunk_funcs.append(fname)

                chunk_row_hs = row_hs_pt[r_start:r_end]
                # row height in drawing units: pt × 0.353mm × 100
                chunk_row_du = [h * ROW_PT_MM * SCALE for h in chunk_row_hs]
                default_rh = max(chunk_row_du) if chunk_row_du else 500
                default_cw = max(col_ws) if col_ws else 2000

                # chunk-local merges
                chunk_merges = []
                for (mr, mc, er, ec) in merges:
                    if mr >= r_end or er < r_start:
                        continue
                    local_mr = max(mr - r_start, 0)
                    local_er = min(er - r_start, chunk_nr - 1)
                    if local_mr <= local_er:
                        chunk_merges.append((local_mr, mc, local_er, ec))

                # chunk-local cells
                chunk_cells = {(r - r_start, c): v for (r, c), v in cells.items()
                               if r_start <= r < r_end}

                chunk_h = sum(chunk_row_du)
                insert_y = y_offset + chunk_h

                a(f'(defun c:{fname} (/ acad doc msp tbl pt)')
                a('  (setq acad (vlax-get-acad-object))')
                a('  (setq doc (vla-get-ActiveDocument acad))')
                a('  (setq msp (vla-get-ModelSpace doc))')
                a(f'  (setq pt (vlax-3d-point 0 {insert_y:.0f} 0))')
                a(f'  (setq tbl (vla-AddTable msp pt {chunk_nr} {ncols} {default_rh:.0f} {default_cw:.0f}))')
                a('  (if (not tbl) (princ " FAILED"))')
                a('  (if tbl (progn')

                a('    (vla-put-StyleName tbl "DsgnTblSt")')
                a('    (vla-put-TitleSuppressed tbl :vlax-true)')
                a(f'    (vla-put-VertCellMargin tbl {FONT_H // 3})')
                a(f'    (vla-put-HorzCellMargin tbl {FONT_H // 3})')

                # Column widths - only non-uniform
                unique_ws = set(round(w) for w in col_ws)
                if len(unique_ws) > 1:
                    for col_i, w in enumerate(col_ws):
                        a(f'    (vla-SetColumnWidth tbl {col_i} {w:.0f})')
                else:
                    a(f'    (vla-SetColumnWidth tbl 0 {col_ws[0]:.0f})')

                # Row heights - only non-default (> default 15pt → 529.5 du)
                default_rh_du = 15 * ROW_PT_MM * SCALE
                for ri, h in enumerate(chunk_row_du):
                    if abs(h - default_rh_du) > 50:  # skip near-default
                        a(f'    (vla-SetRowHeight tbl {ri} {h:.0f})')

                # Cell text
                for (r, c), val in sorted(chunk_cells.items()):
                    txt = esc(val)
                    if not txt:
                        continue
                    a(f'    (vla-SetText tbl {r} {c} "{txt}")')

                # Merges
                a('    (vla-put-RegenerateTableSuppressed tbl :vlax-true)')
                for (mr, mc, er, ec) in chunk_merges:
                    a(f'    (vla-MergeCells tbl {mr} {er} {mc} {ec})')
                a('    (vla-put-RegenerateTableSuppressed tbl :vlax-false)')

                a(f'    (princ "\\n[{fname}] OK {chunk_nr}x{ncols}")')
                a('  ))')
                a(')')

                print(f"    TABLE sec {sec_i} chunk {ci}: rows {r_start}-{r_end-1}, "
                      f"{len(chunk_cells)} cells, {len(chunk_merges)} merges, h={chunk_h/SCALE:.0f}mm")
                y_offset -= (chunk_h + 200)

    # Main function
    dwg_fwd = output_dwg.replace('\\', '/')
    a('(defun c:DesignXlsx2Dwg ()')
    a('  (setvar "FILEDIA" 0)')
    a('  (setvar "CMDECHO" 0)')
    a('  (c:DsgnSetup)')
    for fname in chunk_funcs:
        a(f'  (vl-catch-all-apply \'c:{fname} (list))')
    a('  (setvar "FILEDIA" 0)')
    a(f'  (princ "\\n[SAVEAS] Saving...")')
    a(f'  (command "_.SAVEAS" "2007" "{dwg_fwd}")')
    a('  (princ "\\n[DesignXlsx2Dwg] Done.")')
    a(')')

    return '\n'.join(L)


def convert(xlsx_path, output_dir=None, sheet_name=None, max_rows=None):
    output_dir = output_dir or str(Path(xlsx_path).parent)
    os.makedirs(output_dir, exist_ok=True)

    safe = "dsgn_" + hashlib.md5((Path(xlsx_path).stem + (sheet_name or '')).encode()).hexdigest()[:8]
    dwg_out = os.path.abspath(os.path.join(output_dir, f"{safe}.dwg"))
    if os.path.isfile(dwg_out):
        os.remove(dwg_out)

    print(f"Reading: {xlsx_path}")
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    sn = sheet_name or wb.sheetnames[0]
    print(f"Sheet: {sn}")
    ws = wb[sn]

    print("Analyzing...")
    data = analyze_sheet(ws, max_rows=max_rows)
    print(f"  {data['nrows']}x{data['ncols']}, {len(data['sections'])} sections:")
    for sec in data['sections']:
        s, e = sec['row_start'], sec['row_end']
        if sec['type'] == 'text':
            t = sec.get('text', '')[:40]
            print(f"    MTEXT rows {s}-{e}: {t}")
        else:
            nc = len(sec['cells'])
            nm = len(sec['merges'])
            print(f"    TABLE rows {s}-{e}: {sec['nrows']} rows, {nc} cells, {nm} merges")

    print("\nGenerating LISP...")
    lisp = generate_lisp(data, dwg_out)
    lines = lisp.count('\n')
    print(f"  {len(lisp):,} chars, {lines:,} lines")

    lsp_file = os.path.abspath(os.path.join(output_dir, "_dsgn.lsp"))
    scr_file = os.path.abspath(os.path.join(output_dir, "_dsgn.scr"))

    with open(lsp_file, 'w', encoding='gbk', errors='replace') as f:
        f.write(lisp)

    lsp_fwd = lsp_file.replace('\\', '/')
    with open(scr_file, 'w', encoding='utf-8') as f:
        f.write('(setvar "FILEDIA" 0)\n')
        f.write('(setvar "CMDECHO" 0)\n')
        f.write(f'(load "{lsp_fwd}")\n')
        f.write('(c:DesignXlsx2Dwg)\n')
        f.write('(command "_.QUIT" "Y")\n')

    print(f"\nRunning acad.exe...")
    dwt = A1_TEMPLATE.replace('\\', '/')
    cmd = f'"{ACAD_EXE}" "{dwt}" /b "{scr_file}"'
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600, cwd=output_dir)
        elapsed = time.time() - t0
        print(f"  RC={r.returncode}, {elapsed:.1f}s")
    except subprocess.TimeoutExpired:
        print("  TIMEOUT!")
        return None

    if os.path.isfile(dwg_out):
        sz = os.path.getsize(dwg_out)
        print(f"\nSUCCESS: {dwg_out} ({sz:,} bytes)")
        # Copy to readable name
        readable = os.path.join(output_dir, f"{Path(xlsx_path).stem}_{sn}.dwg")
        try:
            import shutil
            shutil.copy2(dwg_out, readable)
            print(f"Copy: {readable}")
        except:
            pass
        return dwg_out
    else:
        print(f"\nFAILED: {dwg_out} not created")
        return None


if __name__ == "__main__":
    f = sys.argv[1] if len(sys.argv) > 1 else r"C:\opt\ACADxPDF\.test\design-doc-P038215-33264.xlsx"
    out = sys.argv[2] if len(sys.argv) > 2 else r"C:\opt\ACADxPDF\_test_output"
    sheet = sys.argv[3] if len(sys.argv) > 3 else "建筑"
    max_rows = int(sys.argv[4]) if len(sys.argv) > 4 else None
    convert(f, output_dir=out, sheet_name=sheet, max_rows=max_rows)
