"""生成统一的 PMP 纸张表和 LSP 纸张列表
规则:
  A0 系列: +1/8 步进, 最大 +8/8  (base 841×1189)
  A1 系列: +1/4 步进, 最大 +8/4  (base 594×841)
  A2 系列: +1/4 步进, 最大 +8/4  (base 420×594)
  A3 系列: +1/4 步进, 最大 +8/4  (base 297×420)
"""

import json
import math

SERIES = [
    # (name, short, base_long, denominator, max_n)
    ("A3", 297, 420, 4, 8),
    ("A2", 420, 594, 4, 8),
    ("A1", 594, 841, 4, 8),
    ("A0", 841, 1189, 8, 8),
]

MARGIN = 10  # mm

def gen_papers():
    """生成所有纸张尺寸"""
    papers = []
    for name, short, base_long, denom, max_n in SERIES:
        # base size
        papers.append((f"{name}", short, base_long))
        # extended sizes
        for n in range(1, max_n + 1):
            long_mm = round(base_long * (1 + n / denom))
            label = f"{name}+{n}-{denom}"
            papers.append((label, short, long_mm))
    return papers

def make_desc_name(w, h):
    area = w * h
    return f"UserDefinedMetric 纵向 {w}.00W x {h}.00H - (0, 0) x ({w}, {h}) = {area} 毫米"

def make_desc_entry(idx, short, long_mm):
    w = short + MARGIN
    h = long_mm + MARGIN
    area = w * h
    return {
        "caps_type": 2,
        "dimensional": True,
        "media_bounds_urx": float(w),
        "media_bounds_ury": float(h),
        "name": make_desc_name(w, h),
        "printable_area": float(area),
        "printable_bounds_llx": 0.0,
        "printable_bounds_lly": 0.0,
        "printable_bounds_urx": float(w),
        "printable_bounds_ury": float(h),
    }

def make_size_entry(label, short, long_mm):
    w = short + MARGIN
    h = long_mm + MARGIN
    desc_name = make_desc_name(w, h)
    return {
        "caps_type": 2,
        "landscape_mode": False,
        "localized_name": f"{label} {short}x{long_mm}",
        "media_description_name": desc_name,
        "media_group": 15,
        "name": f"UserDefinedMetric ({w}.00 x {h}.00毫米)",
    }

def gen_pmp():
    """生成 PMP 的 description 和 size 部分"""
    papers = gen_papers()
    descriptions = {}
    sizes = {}
    for i, (label, short, long_mm) in enumerate(papers):
        descriptions[str(i)] = make_desc_entry(i, short, long_mm)
        sizes[str(i)] = make_size_entry(label, short, long_mm)
    return descriptions, sizes

def gen_lsp():
    """生成 LSP 纸张列表"""
    papers = gen_papers()
    lines = []
    lines.append(";;; ------------------------------------------------------------")
    lines.append(";;; PC3 可用纸张表 (名称 短边mm 长边mm)")
    lines.append(";;; 统一命名: A0 用 1/8 步进(+1-8..+8-8), 其余 1/4 步进(+1-4..+8-4)")
    lines.append(";;; ------------------------------------------------------------")
    lines.append("(defun ap:pc3-papers ()")
    lines.append("  '(")

    current_series = None
    for label, short, long_mm in papers:
        series = label.split("+")[0] if "+" in label else label
        if series != current_series:
            if current_series is not None:
                lines.append("")
            current_series = series
            comment = f"    ;; --- {series} 系列 ({'1/8' if series == 'A0' else '1/4'} 步进) ---"
            lines.append(comment)
        lines.append(f'    ("{label} {short}x{long_mm}"  {short} {long_mm})')

    lines.append("  ))")
    return "\n".join(lines)

def update_pmp_file(pmp_path):
    """更新 PMP 文件，保留 meta/mod/calibration，替换 description 和 size"""
    with open(pmp_path, "r", encoding="utf-8") as f:
        # 跳过第一行 PIAFILEVERSION 标记
        first_line = f.readline().strip()
        content = f.read()

    data = json.loads(content)
    descriptions, sizes = gen_pmp()

    data["data"]["udm"]["media"]["description"] = descriptions
    data["data"]["udm"]["media"]["size"] = sizes

    # 更新最大纸张尺寸
    max_dim = 0
    for desc in descriptions.values():
        max_dim = max(max_dim, desc["media_bounds_urx"], desc["media_bounds_ury"])
    data["data"]["mod"]["media"]["size_max_x"] = float(max_dim)
    data["data"]["mod"]["media"]["size_max_y"] = float(max_dim)

    output = first_line + "\n" + json.dumps(data, indent=1, ensure_ascii=False)

    with open(pmp_path, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"PMP updated: {len(descriptions)} paper sizes written to {pmp_path}")

def print_table():
    """打印纸张表"""
    papers = gen_papers()
    print(f"{'名称':<20} {'短边':>6} {'长边':>6}  {'公式'}")
    print("-" * 70)
    for label, short, long_mm in papers:
        series = label.split("+")[0] if "+" in label else label
        _, _, base_long, denom, _ = [s for s in SERIES if s[0] == series][0]
        if "+" in label:
            n = int(label.split("+")[1].split("-")[0])
            formula = f"{base_long}×(1+{n}/{denom})={base_long*(1+n/denom):.1f}→{long_mm}"
        else:
            formula = f"base {base_long}"
        print(f"{label:<20} {short:>6} {long_mm:>6}  {formula}")

if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("纸张尺寸表")
    print("=" * 70)
    print_table()

    print("\n" + "=" * 70)
    print("LSP 纸张列表")
    print("=" * 70)
    print(gen_lsp())

    pmp_path = r"C:\opt\ACADxPDF\plot_styles\DWG To PDF.pmp"
    if "--apply" in sys.argv:
        update_pmp_file(pmp_path)
    else:
        print(f"\n[提示] 添加 --apply 参数以更新 PMP 文件: {pmp_path}")
