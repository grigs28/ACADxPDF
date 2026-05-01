"""
ACADxPDF - AutoCAD DWG to PDF batch converter with border detection.

Uses accoreconsole.exe for headless conversion, ezdxf for border analysis.
Supports custom paper sizes based on detected border dimensions.
"""

import os
import sys
import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field


def _is_wsl() -> bool:
    """Detect if running under WSL."""
    try:
        return "microsoft" in open("/proc/version").read().lower()
    except Exception:
        return False


_IN_WSL = _is_wsl()


def _to_native_path(path: str) -> str:
    """Convert path to native format for the current environment.

    WSL: /mnt/c/... <-> C:\\...   (accoreconsole.exe needs Windows paths)
    Windows: pass through unchanged.
    """
    if _IN_WSL:
        # WSL path → Windows path
        m = re.match(r"^/mnt/([a-zA-Z])(/.*)$", path)
        if m:
            drive = m.group(1).upper()
            rest = m.group(2).replace("/", "\\")
            return f"{drive}:{rest}"
        # Already Windows path → keep as-is
        return path
    return path


def _create_work_dir() -> str:
    """为 1 个 DWG 创建 1 个独立工作目录（必须在 Windows 文件系统上）。

    多线程时每个 DWG 必须在独立目录中操作，避免临时文件冲突。
    """
    if WORK_DIR:
        base = WORK_DIR
    else:
        # 项目目录在 /mnt/c 下，accoreconsole 可直接访问
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_work")
    os.makedirs(base, exist_ok=True)
    wd = os.path.join(base, uuid.uuid4().hex[:8])
    os.makedirs(wd, exist_ok=True)
    return wd


# --- Load .env ---
def _load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

_load_env()

# --- Constants (from .env with fallbacks) ---
ACCORE = os.environ.get("ACAD_PATH", r"C:\Autodesk\AutoCAD 2020\accoreconsole.exe")
# 工作目录：accoreconsole 的临时文件放在这里（必须在 Windows 文件系统上）
# 项目在 /opt 时，accoreconsole 无法直接访问 Linux 文件系统，需要此目录中转
WORK_DIR = os.environ.get("WORK_DIR", "")
MM = os.environ.get("ACAD_UNIT", "毫米")
DEFAULT_PRINTER = os.environ.get("PRINTER", "DWG To PDF.pc3")
DEFAULT_PLOT_STYLE = os.environ.get("PLOT_STYLE", "monochrome.ctb")
DEFAULT_TIMEOUT = int(os.environ.get("TIMEOUT", "180"))
BORDER_KEYWORDS = [k.strip() for k in os.environ.get(
    "BORDER_KEYWORDS", "TK,TUKUANG,BORDER,FRAME,TITLE").split(",")]

STANDARD_SIZES = {
    "A0": (841, 1189),
    "A1": (594, 841),
    "A2": (420, 594),
    "A3": (297, 420),
    "A4": (210, 297),
}


@dataclass
class Border:
    """Detected drawing border."""
    name: str
    x: float
    y: float
    width: float   # in DWG units (1 unit = 1mm typically)
    height: float
    insert_x: float
    insert_y: float
    # Internal block bbox offset (needed for Window mode coordinates)
    bbox_min_x: float = 0.0
    bbox_min_y: float = 0.0
    bbox_max_x: float = 0.0
    bbox_max_y: float = 0.0
    xscale: float = 1.0
    yscale: float = 1.0

    @property
    def paper_width_mm(self) -> float:
        return self.width / 100

    @property
    def paper_height_mm(self) -> float:
        return self.height / 100

    @property
    def standard_size(self) -> str:
        """Match to closest standard size, return name or 'custom'."""
        h = self.paper_height_mm
        for name, (w_min, w_max) in STANDARD_SIZES.items():
            if abs(h - w_min) < 5 or abs(h - w_max) < 5:
                return name
        return "custom"

    @property
    def orientation(self) -> str:
        return "L" if self.width > self.height else "P"

    @property
    def size_label(self) -> str:
        """Paper size label like 'A1', 'A1+0.5', 'A1+1', 'custom'."""
        short_side = min(self.paper_width_mm, self.paper_height_mm)
        long_side = max(self.paper_width_mm, self.paper_height_mm)

        # Find base size by matching short side
        base = None
        for name, (sw, sh) in STANDARD_SIZES.items():
            if abs(short_side - sw) < 50:
                base = (name, sw, sh)
                break

        if not base:
            return "custom"

        name, sw, standard_long = base

        # Check if standard (no elongation)
        if abs(long_side - standard_long) < 10:
            return name

        # Calculate elongation: (extended_long - standard_long) / standard_long
        ratio = (long_side - standard_long) / standard_long
        ratio = round(ratio * 2) / 2  # round to nearest 0.5

        if ratio <= 0:
            return name
        if ratio == int(ratio):
            return f"{name}+{int(ratio)}"
        return f"{name}+{ratio}"

    def world_bbox(self) -> tuple[float, float, float, float]:
        """Get bounding box in world coordinates (lower-left x,y, upper-right x,y)."""
        x0 = self.insert_x + self.bbox_min_x * self.xscale
        y0 = self.insert_y + self.bbox_min_y * self.yscale
        x1 = self.insert_x + self.bbox_max_x * self.xscale
        y1 = self.insert_y + self.bbox_max_y * self.yscale
        return (x0, y0, x1, y1)

    @property
    def paper_size_name(self) -> str:
        """Generate the paper size name for -PLOT command."""
        w = self.paper_width_mm
        h = self.paper_height_mm
        if self.orientation == "L":
            return f"ISO full bleed A1 ({w:.2f} x {h:.2f} {MM})"
        return f"ISO full bleed A1 ({w:.2f} x {h:.2f} {MM})"


@dataclass
class ConversionResult:
    """Result of a DWG→PDF conversion."""
    dwg_path: str
    pdf_path: str = ""
    dxf_path: str = ""
    borders: list = field(default_factory=list)
    success: bool = False
    error: str = ""
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        return {
            "dwg": self.dwg_path,
            "dxf": self.dxf_path,
            "pdf": self.pdf_path,
            "success": self.success,
            "error": self.error,
            "elapsed": round(self.elapsed, 1),
            "borders": [
                {
                    "name": b.name,
                    "width_mm": round(b.paper_width_mm, 0),
                    "height_mm": round(b.paper_height_mm, 0),
                    "size_label": b.size_label,
                    "orientation": b.orientation,
                }
                for b in self.borders
            ],
        }


def _safe_ascii_copy(src: str, dst_dir: str) -> str:
    """Copy file to dst_dir with an ASCII-safe name if path contains non-ASCII chars."""
    try:
        src.encode('ascii')
        return src
    except UnicodeEncodeError:
        pass
    import shutil
    safe_name = f"_input_{uuid.uuid4().hex[:8]}.dwg"
    dst = os.path.join(dst_dir, safe_name)
    shutil.copy2(src, dst)
    return dst


def dwg_to_dxf(dwg_path: str, work_dir: str) -> str:
    """Convert DWG to DXF using accoreconsole. 在指定的 work_dir 中操作。"""
    dwg_path = os.path.abspath(dwg_path)
    if not os.path.exists(ACCORE):
        raise FileNotFoundError(f"accoreconsole.exe not found: {ACCORE}")

    os.makedirs(work_dir, exist_ok=True)

    dwg_win = _to_native_path(dwg_path)
    dxf_file = os.path.join(work_dir, f"_temp_{uuid.uuid4().hex[:6]}.dxf")
    dxf_win = _to_native_path(dxf_file).replace("\\", "/")

    scr_content = f'(command "_.FILEDIA" "0")\n(command "_.CMDDIA" "0")\n(command "_.SAVEAS" "DXF" "" "{dxf_win}")\n(command "_.QUIT" "N")\n'
    scr_path = os.path.join(work_dir, f"_dwg2dxf_{uuid.uuid4().hex[:6]}.scr")
    with open(scr_path, "w", encoding="utf-8") as f:
        f.write(scr_content)

    cmd = [ACCORE, "/i", dwg_win, "/s", _to_native_path(scr_path), "/l", "en-US"]
    subprocess.run(cmd, capture_output=True, timeout=DEFAULT_TIMEOUT)

    try:
        os.remove(scr_path)
    except OSError:
        pass

    if not os.path.exists(dxf_file):
        raise RuntimeError(f"DXF not created: {dxf_file}")
    return dxf_file


def _is_rect(pts: list, tol: float = 1.0) -> bool:
    """Check if points form an axis-aligned rectangle."""
    if len(pts) < 4:
        return False
    xs = sorted(set(round(p[0] / tol) * tol for p in pts[:5]))
    ys = sorted(set(round(p[1] / tol) * tol for p in pts[:5]))
    return len(xs) == 2 and len(ys) == 2


def _block_bbox(block) -> tuple[float, float, float, float]:
    """Compute bounding box of all entities in a block definition."""
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for e in block:
        try:
            if e.dxftype() == "LINE":
                x0 = min(x0, e.dxf.start.x, e.dxf.end.x)
                x1 = max(x1, e.dxf.start.x, e.dxf.end.x)
                y0 = min(y0, e.dxf.start.y, e.dxf.end.y)
                y1 = max(y1, e.dxf.start.y, e.dxf.end.y)
            elif e.dxftype() == "LWPOLYLINE":
                for pt in e.get_points(format="xy"):
                    x0 = min(x0, pt[0])
                    x1 = max(x1, pt[0])
                    y0 = min(y0, pt[1])
                    y1 = max(y1, pt[1])
        except Exception:
            pass
    if x0 >= float("inf"):
        return (0, 0, 0, 0)
    return (x0, y0, x1, y1)


def _has_edge_lines(block, bbox: tuple, tol: float = 1.0) -> bool:
    """Check if LINEs in block cover all 4 edges of bbox (>=90% each)."""
    bx0, by0, bx1, by1 = bbox
    full_w = bx1 - bx0
    full_h = by1 - by0
    if full_w < 1 or full_h < 1:
        return False
    edges = {"B": 0.0, "T": 0.0, "L": 0.0, "R": 0.0}
    for e in block:
        if e.dxftype() != "LINE":
            continue
        sx, sy = e.dxf.start.x, e.dxf.start.y
        ex, ey = e.dxf.end.x, e.dxf.end.y
        if abs(sy - by0) < tol and abs(ey - by0) < tol:
            edges["B"] += abs(ex - sx)
        elif abs(sy - by1) < tol and abs(ey - by1) < tol:
            edges["T"] += abs(ex - sx)
        elif abs(sx - bx0) < tol and abs(ex - bx0) < tol:
            edges["L"] += abs(ey - sy)
        elif abs(sx - bx1) < tol and abs(ex - bx1) < tol:
            edges["R"] += abs(ey - sy)
    return (edges["B"] >= full_w * 0.9 and edges["T"] >= full_w * 0.9 and
            edges["L"] >= full_h * 0.9 and edges["R"] >= full_h * 0.9)


def _to_world(ix: float, iy: float, sx: float, sy: float,
              lx0: float, ly0: float, lx1: float, ly1: float):
    """Transform block-local coords to world coords via INSERT."""
    wx0, wy0 = ix + lx0 * sx, iy + ly0 * sy
    wx1, wy1 = ix + lx1 * sx, iy + ly1 * sy
    return (min(wx0, wx1), min(wy0, wy1), max(wx0, wx1), max(wy0, wy1))


def _contains(a: tuple, b: tuple, tol: float = 1.0) -> bool:
    """Check if rect a contains rect b (with tolerance)."""
    return (a[0] - tol <= b[0] and a[1] - tol <= b[1] and
            a[2] + tol >= b[2] and a[3] + tol >= b[3])


def _matches_standard(short_mm: float, tolerance: float = 20.0) -> bool:
    """Check if a short side in mm matches a standard A3+ paper size."""
    for _, (sw, _) in STANDARD_SIZES.items():
        if sw < 280:
            continue
        if abs(short_mm - sw) < tolerance:
            return True
    return False


def detect_rect_borders(dxf_path: str) -> list[Border]:
    """Detect outermost closed rectangles as drawing borders.

    Scans modelspace and block definitions for:
    - Closed LWPOLYLINE with 4 axis-aligned vertices
    - Block bboxes with LINEs covering all 4 edges
    Filters by standard A3+ paper size matching, then removes inner rectangles.
    """
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    rectangles = []  # list of (x0, y0, x1, y1, source)

    # 1. Closed LWPOLYLINE in modelspace
    for e in msp.query("LWPOLYLINE"):
        if not e.closed:
            continue
        pts = list(e.get_points(format="xy"))
        if not _is_rect(pts):
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        rx0, ry0, rx1, ry1 = min(xs), min(ys), max(xs), max(ys)
        short_mm = min(rx1 - rx0, ry1 - ry0) / 100
        if _matches_standard(short_mm):
            rectangles.append((rx0, ry0, rx1, ry1, "mspace_lwpoly"))

    # 2. Rectangles from block definitions (via ALL INSERT entities)
    # Cache block content checks (same block, different positions)
    block_cache = {}  # name -> (has_edge_lines, bbox, has_std_rect, rect_local_bboxes)
    for ins in msp.query("INSERT"):
        name = ins.dxf.name
        block = doc.blocks.get(name)
        if not block:
            continue

        if name not in block_cache:
            bb = _block_bbox(block)
            has_edges = bb[2] - bb[0] > 0 and _has_edge_lines(block, bb)
            # Check for closed LWPOLYLINE rectangles inside block
            rect_locals = []
            for e in block:
                if e.dxftype() != "LWPOLYLINE" or not e.closed:
                    continue
                pts = list(e.get_points(format="xy"))
                if not _is_rect(pts):
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                rect_locals.append((min(xs), min(ys), max(xs), max(ys)))
            block_cache[name] = (has_edges, bb, rect_locals)

        has_edges, bb, rect_locals = block_cache[name]
        xs_ins = ins.dxf.get("xscale", 1)
        ys_ins = ins.dxf.get("yscale", 1)
        ix = ins.dxf.insert.x
        iy = ins.dxf.insert.y

        # 2a. Closed LWPOLYLINE inside block
        for lx0, ly0, lx1, ly1 in rect_locals:
            world = _to_world(ix, iy, xs_ins, ys_ins, lx0, ly0, lx1, ly1)
            short_mm = min(world[2] - world[0], world[3] - world[1]) / 100
            if _matches_standard(short_mm):
                rectangles.append((*world, f"block_poly:{name}"))

        # 2b. LINE-covered bbox rectangle
        if has_edges:
            world = _to_world(ix, iy, xs_ins, ys_ins, *bb)
            short_mm = min(world[2] - world[0], world[3] - world[1]) / 100
            if _matches_standard(short_mm):
                rectangles.append((*world, f"block_lines:{name}"))

    if not rectangles:
        return []

    # 3. Containment filtering: remove rectangles contained by another
    outermost = []
    for i, ri in enumerate(rectangles):
        contained = False
        for j, rj in enumerate(rectangles):
            if i == j:
                continue
            if ri == rj and j < i:
                continue  # deduplicate: keep first occurrence
            if _contains(rj, ri) and rj != ri:
                contained = True
                break
        if not contained:
            outermost.append(ri)

    # 4. Convert to Border objects
    borders = []
    for rx0, ry0, rx1, ry1, source in outermost:
        w = rx1 - rx0
        h = ry1 - ry0
        borders.append(Border(
            name=f"rect:{source}",
            x=rx0, y=ry0,
            width=w, height=h,
            insert_x=rx0, insert_y=ry0,
            bbox_min_x=0, bbox_min_y=0,
            bbox_max_x=w, bbox_max_y=h,
            xscale=1, yscale=1,
        ))

    return borders


def detect_borders(dxf_path: str, min_border_ratio: float = 0.3) -> list[Border]:
    """Detect drawing borders. Tries block name matching first, falls back to rectangle detection."""
    borders = detect_block_borders(dxf_path, min_border_ratio)
    if borders:
        return borders
    return detect_rect_borders(dxf_path)


def detect_block_borders(dxf_path: str, min_border_ratio: float = 0.3) -> list[Border]:
    """Detect drawing borders via INSERT block short-side matching (original method)."""
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    # Get drawing extents for filtering
    extmin = doc.header.get("$EXTMIN", (0, 0, 0))
    extmax = doc.header.get("$EXTMAX", (0, 0, 0))
    ext_h = abs(extmax[1] - extmin[1]) if hasattr(extmin, "__getitem__") else 0
    min_border_h = ext_h * min_border_ratio

    borders = []

    for ins in msp.query("INSERT"):
        name = ins.dxf.name
        block = doc.blocks.get(name)
        if not block:
            continue

        # Calculate block bounding box
        bx0 = by0 = float("inf")
        bx1 = by1 = float("-inf")
        for e in block:
            try:
                if e.dxftype() == "LINE":
                    bx0 = min(bx0, e.dxf.start.x, e.dxf.end.x)
                    bx1 = max(bx1, e.dxf.start.x, e.dxf.end.x)
                    by0 = min(by0, e.dxf.start.y, e.dxf.end.y)
                    by1 = max(by1, e.dxf.start.y, e.dxf.end.y)
                elif e.dxftype() == "LWPOLYLINE":
                    for pt in e.get_points(format="xy"):
                        bx0 = min(bx0, pt[0])
                        bx1 = max(bx1, pt[0])
                        by0 = min(by0, pt[1])
                        by1 = max(by1, pt[1])
            except Exception:
                pass

        if bx0 >= float("inf"):
            continue

        xs = ins.dxf.get("xscale", 1)
        ys = ins.dxf.get("yscale", 1)
        bw = (bx1 - bx0) * abs(xs)
        bh = (by1 - by0) * abs(ys)

        # Filter: only keep blocks whose short side matches A3+ paper size (>=280mm)
        # This filters out small content blocks, title blocks, symbols etc.
        short_side = min(bw, bh) / 100  # in mm
        is_border = False
        for _name, (sw, sh) in STANDARD_SIZES.items():
            if sw < 280:  # skip A4 (210) and smaller
                continue
            if abs(short_side - sw) < 20:  # short side matches within 20mm
                is_border = True
                break
        if not is_border:
            continue

        ix = ins.dxf.insert.x
        iy = ins.dxf.insert.y

        borders.append(Border(
            name=name,
            x=ix, y=iy,
            width=bw, height=bh,
            insert_x=ix, insert_y=iy,
            bbox_min_x=bx0, bbox_min_y=by0,
            bbox_max_x=bx1, bbox_max_y=by1,
            xscale=xs, yscale=ys,
        ))

    return borders


def merge_nearby_borders(borders: list[Border], gap_ratio: float = 1.0) -> list[list[Border]]:
    """Group nearby borders into clusters using union-find.

    Borders within gap_ratio * min_dimension of each other are merged
    into the same group (they likely belong to the same drawing sheet).
    """
    if len(borders) <= 1:
        return [borders] if borders else []

    bboxes = [b.world_bbox() for b in borders]
    parent = list(range(len(borders)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        pi, pj = find(i), find(j)
        if pi != pj:
            parent[pi] = pj

    for i in range(len(borders)):
        x0i, y0i, x1i, y1i = bboxes[i]
        dim_i = min(x1i - x0i, y1i - y0i)

        for j in range(i + 1, len(borders)):
            x0j, y0j, x1j, y1j = bboxes[j]
            dim_j = min(x1j - x0j, y1j - y0j)
            tol = min(dim_i, dim_j) * gap_ratio

            if (x0i <= x1j + tol and x1i >= x0j - tol and
                    y0i <= y1j + tol and y1i >= y0j - tol):
                union(i, j)

    groups = {}
    for i in range(len(borders)):
        groups.setdefault(find(i), []).append(borders[i])

    return list(groups.values())


def compute_size_label(w_mm: float, h_mm: float) -> str:
    """Compute size label like 'A1', 'A1+0.5' from dimensions in mm."""
    short_side = min(w_mm, h_mm)
    long_side = max(w_mm, h_mm)

    base = None
    for name, (sw, sh) in STANDARD_SIZES.items():
        if sw < 280:
            continue
        if abs(short_side - sw) < 50:
            base = (name, sw, sh)
            break

    if not base:
        return "custom"

    name, sw, standard_long = base

    if abs(long_side - standard_long) < 10:
        return name

    ratio = (long_side - standard_long) / standard_long
    ratio = round(ratio * 2) / 2

    if ratio <= 0:
        return name
    if ratio == int(ratio):
        return f"{name}+{int(ratio)}"
    return f"{name}+{ratio}"


def get_paper_size_name(width_mm: float, height_mm: float, orientation: str) -> str:
    """Generate paper size name for -PLOT command.

    For standard sizes, uses the built-in name.
    For custom/elongated sizes, falls back to the nearest larger standard size.
    """
    # Check standard sizes
    w, h = (max(width_mm, height_mm), min(width_mm, height_mm))
    for name, (sw, sh) in STANDARD_SIZES.items():
        if abs(w - sh) < 5 and abs(h - sw) < 5:
            return f"ISO full bleed {name} ({width_mm:.2f} x {height_mm:.2f} {MM})"

    # Custom/elongated: find smallest standard size that fits
    best = None
    for name, (sw, sh) in reversed(list(STANDARD_SIZES.items())):
        if w <= sh and h <= sw:
            best = (name, sw, sh)
            break  # reversed() iterates A4→A0, first match = smallest that fits

    if best:
        name, sw, sh = best
        if orientation == "L":
            return f"ISO full bleed {name} ({sh:.2f} x {sw:.2f} {MM})"
        return f"ISO full bleed {name} ({sw:.2f} x {sh:.2f} {MM})"

    # Fallback to A0 (largest standard)
    return f"ISO full bleed A0 (841.00 x 1189.00 {MM})"


def generate_plot_script(
    output_pdf: str,
    paper_size_name: str,
    orientation: str = "L",
    printer: str = "DWG To PDF.pc3",
    plot_style: str = "monochrome.ctb",
    window: tuple[float, float, float, float] = None,
) -> str:
    """Generate AutoLISP -PLOT script content."""
    orient_code = orientation[0].upper()
    output_fwd = _to_native_path(output_pdf).replace("\\", "/")

    if window:
        x0, y0, x1, y1 = window
        # Window mode: W then two corner points as separate responses
        lisp = (
            f'(command "_.FILEDIA" "0")\n'
            f'(command "_.CMDDIA" "0")\n'
            f'(command "_.EXPERT" "1")\n'
            f'(command "_.-PLOT" "Y" "" "{printer}" "{paper_size_name}" '
            f'"M" "{orient_code}" "N" '
            f'"W" "{x0:.2f},{y0:.2f}" "{x1:.2f},{y1:.2f}" '
            f'"F" "C" "Y" "{plot_style}" "N" "" "{output_fwd}" "N" "Y")\n'
            f'(command "_.QUIT" "N")\n'
        )
    else:
        # Extents mode - proven to work
        lisp = (
            f'(command "_.FILEDIA" "0")\n'
            f'(command "_.CMDDIA" "0")\n'
            f'(command "_.EXPERT" "1")\n'
            f'(command "_.-PLOT" "Y" "" "{printer}" "{paper_size_name}" '
            f'"M" "{orient_code}" "N" "E" "F" "C" "Y" "{plot_style}" '
            f'"N" "" "{output_fwd}" "N" "Y")\n'
            f'(command "_.QUIT" "N")\n'
        )
    return lisp


def run_conversion(dwg_path: str, script_content: str, work_dir: str, timeout: int = 120) -> bool:
    """Run accoreconsole with a given script. 在指定的 work_dir 中操作。"""
    dwg_win = _to_native_path(dwg_path)

    scr_path = os.path.join(work_dir, f"_plot_{uuid.uuid4().hex[:6]}.scr")
    with open(scr_path, "w", encoding="utf-8-sig") as f:
        f.write(script_content)

    cmd = [ACCORE, "/i", dwg_win, "/s", _to_native_path(scr_path), "/l", "en-US"]
    subprocess.run(cmd, capture_output=True, timeout=timeout)

    try:
        os.remove(scr_path)
    except OSError:
        pass
    return True


def _emit(callback, event: str, data: dict):
    if callback:
        callback(event, data)


def convert_dwg(
    dwg_path: str,
    output_dir: str = "./output",
    printer: str = None,
    plot_style: str = None,
    split_borders: bool = True,
    auto_paper_size: bool = True,
    merge_borders: bool = False,
    paper_size: str = None,
    orientation: str = None,
    timeout: int = None,
    progress_callback=None,
) -> ConversionResult:
    """Convert a DWG file to PDF with optional border detection and splitting.

    Args:
        dwg_path: Path to input DWG file.
        output_dir: Output directory for PDF files.
        printer: PC3 printer name.
        plot_style: CTB plot style name.
        split_borders: Detect borders and split into separate PDFs.
        auto_paper_size: Auto-detect paper size from border dimensions.
        merge_borders: Merge nearby borders into one PDF (else each border = 1 PDF).
        paper_size: Override paper size name (e.g. "ISO full bleed A1 (841.00 x 594.00 毫米)").
        orientation: Override orientation "L" or "P".
        timeout: Timeout in seconds for accoreconsole.

    Returns:
        ConversionResult with details.
    """
    printer = printer or DEFAULT_PRINTER
    plot_style = plot_style or DEFAULT_PLOT_STYLE
    timeout = timeout or DEFAULT_TIMEOUT
    start = time.time()
    dwg_path = os.path.abspath(dwg_path)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # 1 个 DWG = 1 个工作目录，所有操作共用
    work_dir = _create_work_dir()

    result = ConversionResult(dwg_path=dwg_path)

    try:
        # 复制 DWG 到工作目录（仅一次）
        safe_dwg = _safe_ascii_copy(dwg_path, work_dir)

        if split_borders:
            # Step 1: DWG → DXF
            _emit(progress_callback, "progress", {"step": "dwg_to_dxf", "file": os.path.basename(dwg_path)})
            dxf_path = dwg_to_dxf(safe_dwg, work_dir)
            result.dxf_path = dxf_path

            # Step 2: Detect borders
            _emit(progress_callback, "progress", {"step": "detect_borders", "file": os.path.basename(dwg_path)})
            borders = detect_borders(dxf_path)
            result.borders = borders
            _emit(progress_callback, "borders", {
                "file": os.path.basename(dwg_path),
                "count": len(borders),
                "borders": [
                    {"name": b.name, "size_label": b.size_label,
                     "width_mm": round(b.paper_width_mm), "height_mm": round(b.paper_height_mm)}
                    for b in borders
                ],
            })

            if not borders:
                ps = paper_size or get_paper_size_name(841, 594, "L")
                ori = orientation or "L"
                pdf_name = f"{Path(dwg_path).stem}.pdf"
                pdf_path = os.path.join(output_dir, pdf_name)
                pdf_temp = os.path.join(work_dir, "_temp.pdf")
                script = generate_plot_script(pdf_temp, ps, ori, printer, plot_style)
                ok = run_conversion(safe_dwg, script, work_dir, timeout)
                if ok and os.path.exists(pdf_temp):
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)
                    shutil.move(pdf_temp, pdf_path)
                    result.pdf_path = pdf_path
                    result.success = True
            else:
                # Step 3: 生成所有图框的 -PLOT 命令，合并成 1 个脚本，1 次 accoreconsole
                if merge_borders:
                    groups = merge_nearby_borders(borders)
                else:
                    groups = [[b] for b in borders]

                target_dir = work_dir
                plot_commands = '(command "_.FILEDIA" "0")\n(command "_.CMDDIA" "0")\n(command "_.EXPERT" "1")\n'
                pdf_map = []  # [(temp_path, final_path), ...]

                for i, group in enumerate(groups):
                    if len(group) == 1:
                        border = group[0]
                        w_mm = border.paper_width_mm
                        h_mm = border.paper_height_mm
                        ori = orientation or border.orientation
                        label = border.size_label
                        bx0, by0, bx1, by1 = border.world_bbox()
                    else:
                        all_bb = [b.world_bbox() for b in group]
                        bx0 = min(bb[0] for bb in all_bb)
                        by0 = min(bb[1] for bb in all_bb)
                        bx1 = max(bb[2] for bb in all_bb)
                        by1 = max(bb[3] for bb in all_bb)
                        w_mm = (bx1 - bx0) / 100
                        h_mm = (by1 - by0) / 100
                        ori = orientation or ("L" if w_mm > h_mm else "P")
                        label = compute_size_label(w_mm, h_mm)

                    if auto_paper_size and not paper_size:
                        ps = get_paper_size_name(w_mm, h_mm, ori)
                    else:
                        ps = paper_size or get_paper_size_name(841, 594, "L")

                    pdf_name = f"{i+1:02d}-{Path(dwg_path).stem}-{label}.pdf"
                    pdf_path = os.path.join(output_dir, pdf_name)
                    pdf_temp = os.path.join(target_dir, f"_temp_{i}.pdf")
                    pdf_win = _to_native_path(pdf_temp).replace("\\", "/")

                    plot_commands += (
                        f'(command "_.-PLOT" "Y" "" "{printer}" "{ps}" '
                        f'"M" "{ori[0].upper()}" "N" '
                        f'"W" "{bx0:.2f},{by0:.2f}" "{bx1:.2f},{by1:.2f}" '
                        f'"F" "C" "Y" "{plot_style}" "N" "" "{pdf_win}" "N" "Y")\n'
                    )
                    pdf_map.append((pdf_temp, pdf_path))

                    _emit(progress_callback, "progress", {
                        "step": "plot_pdf", "file": os.path.basename(dwg_path),
                        "border_index": i + 1, "total_borders": len(groups),
                        "size_label": label,
                    })

                plot_commands += '(command "_.QUIT" "N")\n'

                # 1 次 accoreconsole 生成所有 PDF
                ok = run_conversion(safe_dwg, plot_commands, target_dir, timeout)

                all_ok = True
                pdf_files = []
                for pdf_temp, pdf_path in pdf_map:
                    if os.path.exists(pdf_temp):
                        if os.path.exists(pdf_path):
                            os.remove(pdf_path)
                        shutil.move(pdf_temp, pdf_path)
                        pdf_files.append(pdf_path)
                    elif os.path.exists(pdf_path):
                        pdf_files.append(pdf_path)
                    else:
                        all_ok = False

                if pdf_files:
                    result.pdf_path = pdf_files[0] if len(pdf_files) == 1 else json.dumps(pdf_files)
                    result.success = all_ok
                    _emit(progress_callback, "done", {
                        "file": os.path.basename(dwg_path),
                        "pdf_count": len(pdf_files),
                        "success": all_ok,
                    })
        else:
            # Simple conversion without border detection
            ps = paper_size or get_paper_size_name(841, 594, "L")
            ori = orientation or "L"
            pdf_name = f"{Path(dwg_path).stem}.pdf"
            pdf_path = os.path.join(output_dir, pdf_name)
            pdf_temp = os.path.join(work_dir, "_temp.pdf")
            script = generate_plot_script(pdf_temp, ps, ori, printer, plot_style)
            ok = run_conversion(safe_dwg, script, work_dir, timeout)
            if ok and os.path.exists(pdf_temp):
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
                shutil.move(pdf_temp, pdf_path)
                result.pdf_path = pdf_path
                result.success = True

    except Exception as ex:
        result.error = str(ex)
        _emit(progress_callback, "error", {"file": os.path.basename(dwg_path), "error": str(ex)})
    finally:
        result.elapsed = time.time() - start
        # 清理工作目录中的临时文件
        if os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

    return result


def batch_convert(
    input_dir: str,
    output_dir: str = "./output",
    progress_callback=None,
    **kwargs,
) -> list[ConversionResult]:
    """Batch convert all DWG files in a directory."""
    results = []
    dwg_files = sorted(Path(input_dir).glob("*.dwg"))
    total = len(dwg_files)

    print(f"Found {total} DWG files in {input_dir}")
    _emit(progress_callback, "batch_start", {"total": total})

    for i, dwg in enumerate(dwg_files, 1):
        print(f"\n[{i}/{total}] {dwg.name}")
        _emit(progress_callback, "batch_file", {"index": i, "total": total, "file": dwg.name})

        def _cb(event, data, idx=i):
            data["batch_index"] = idx
            data["batch_total"] = total
            _emit(progress_callback, event, data)

        r = convert_dwg(str(dwg), output_dir, progress_callback=_cb, **kwargs)
        results.append(r)

    # Summary
    ok = sum(1 for r in results if r.success)
    total_pdfs = 0
    for r in results:
        if r.success:
            total_pdfs += len(r.borders) if r.borders else 1
    total_time = sum(r.elapsed for r in results)
    avg_time = total_time / len(results) if results else 0
    avg_pdf = f"{total_time/total_pdfs:.1f}s/PDF" if total_pdfs else "N/A"
    print(f"\n=== Done: {ok}/{len(results)} DWG | {total_pdfs} PDFs | "
          f"Total {total_time:.1f}s | Avg {avg_time:.1f}s/DWG | {avg_pdf} ===")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ACADxPDF - DWG to PDF converter")
    parser.add_argument("input", help="DWG file or directory")
    parser.add_argument("-o", "--output", default="./output", help="Output directory")
    parser.add_argument("--no-split", action="store_true", help="Don't split by borders")
    parser.add_argument("--no-auto-size", action="store_true", help="Don't auto-detect paper size")
    parser.add_argument("--paper", help="Override paper size name")
    parser.add_argument("--orientation", choices=["L", "P"], help="Override orientation")
    parser.add_argument("--printer", default=DEFAULT_PRINTER)
    parser.add_argument("--style", default=DEFAULT_PLOT_STYLE)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    if os.path.isdir(args.input):
        batch_convert(
            args.input, args.output,
            printer=args.printer, plot_style=args.style,
            split_borders=not args.no_split,
            auto_paper_size=not args.no_auto_size,
            paper_size=args.paper, orientation=args.orientation,
            timeout=args.timeout,
        )
    else:
        r = convert_dwg(
            args.input, args.output,
            printer=args.printer, plot_style=args.style,
            split_borders=not args.no_split,
            auto_paper_size=not args.no_auto_size,
            paper_size=args.paper, orientation=args.orientation,
            timeout=args.timeout,
        )
        if r.success:
            print(f"Success: {r.pdf_path}")
        else:
            print(f"Failed: {r.error}")
            sys.exit(1)
