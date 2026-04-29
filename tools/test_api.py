import sys
sys.path.insert(0, r"C:\opt\ACADxPDF")
from acad2pdf import convert_dwg, detect_borders, dwg_to_dxf
import os

# Test full pipeline on 1.dwg
dwg = r"C:\opt\ACADxPDF\t3\1.dwg"
out = r"C:\opt\ACADxPDF\output"

print("Step 1: DWG -> DXF")
dxf = dwg_to_dxf(dwg, out)
print(f"  DXF: {dxf}")

print("\nStep 2: Detect borders")
borders = detect_borders(dxf)
for b in borders:
    print(f"  {b.name}: {b.paper_width_mm:.0f}x{b.paper_height_mm:.0f}mm "
          f"({b.standard_size} {b.orientation}) "
          f"at ({b.insert_x:.0f},{b.insert_y:.0f})")

print("\nStep 3: Convert to PDF")
r = convert_dwg(dwg, out, split_borders=True, auto_paper_size=True, timeout=120)
print(f"  Success: {r.success}")
print(f"  PDF: {r.pdf_path}")
print(f"  Borders: {len(r.borders)}")
print(f"  Time: {r.elapsed:.1f}s")
if r.error:
    print(f"  Error: {r.error}")
