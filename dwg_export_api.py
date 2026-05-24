"""DWG 评分表导出 API

接收 JSON 评分数据 → ezdxf 生成表格 DXF → accoreconsole 转 DWG → 返回文件。

Usage:
    python dwg_export_api.py
    # 监听 http://0.0.0.0:8901

Architecture:
    JSON → ezdxf (DXF: LINE网格 + MTEXT单元格) → accoreconsole (DXF→DWG) → DWG文件流
"""

import os
import uuid
import subprocess
import tempfile
import logging
from pathlib import Path

from flask import Flask, request, Response, jsonify
import ezdxf

# --- Config ---
ACCCORE = os.environ.get("ACAD_PATH", r"C:\opt\AutoCAD 2026\accoreconsole.exe")
API_HOST = os.environ.get("EXPORT_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("EXPORT_API_PORT", "8901"))
CONVERT_TIMEOUT = 60

log = logging.getLogger("dwg_export")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__)

WORK_DIR = Path(tempfile.gettempdir()) / "dwg_export"
WORK_DIR.mkdir(exist_ok=True)


# ── DXF 生成 ────────────────────────────────────────────────

def generate_dxf(data: dict) -> str:
    """根据评分数据生成含表格的 DXF 文件。"""
    doc = ezdxf.new("R2007", setup=True)
    msp = doc.modelspace()

    # 中文文字样式 — 统一宋体
    if "SimSun" not in [s.dxf.name for s in doc.styles]:
        doc.styles.add("SimSun", font="simsun.ttc")

    for table_data in data.get("tables", []):
        _draw_table(msp, table_data, doc)

    dxf_path = str(WORK_DIR / f"t_{uuid.uuid4().hex[:8]}.dxf")
    doc.saveas(dxf_path)
    return dxf_path


def _draw_table(msp, td: dict, doc):
    """绘制一个评分表：LINE 网格 + MTEXT 单元格文字。"""
    x0, y0 = td.get("insert_point", [0, 0])
    col_ws = td.get("col_widths", [])
    rows = td.get("rows", [])
    hdr_h = td.get("header_height", 8.0)
    row_h = td.get("row_height", 12.0)

    if not col_ws or not rows:
        return

    # 图层
    doc.layers.add("评分表", color=7)

    # 每行高度
    rh_list = []
    for r in rows:
        t = r.get("type", "score")
        if t == "header":
            rh_list.append(hdr_h)
        elif t == "subtotal":
            rh_list.append(row_h * 1.1)
        else:
            rh_list.append(row_h)

    total_w = sum(col_ws)
    total_h = sum(rh_list)

    # ── 网格线 ──
    def _hline(y, lw):
        msp.add_line((x0, y), (x0 + total_w, y),
                     dxfattribs={"layer": "评分表", "lineweight": lw})

    def _vline(x, lw):
        msp.add_line((x, y0), (x, y0 - total_h),
                     dxfattribs={"layer": "评分表", "lineweight": lw})

    # 水平线
    y = y0
    for i, rh in enumerate(rh_list):
        lw = 50 if i == 0 else 13          # 首行粗线
        _hline(y, lw)
        y -= rh
    _hline(y, 50)                           # 底部粗线

    # 垂直线
    x = x0
    for cw in col_ws:
        _vline(x, 13)
        x += cw
    _vline(x, 50)                           # 右侧粗线

    # ── 单元格文字 ──
    y = y0
    for i, (rh, row) in enumerate(zip(rh_list, rows)):
        rtype = row.get("type", "score")
        cells = row.get("cells", [])
        x = x0

        th = 3.5 if rtype == "header" else 2.8   # 字高
        sty = "SimSun"

        for j, cw in enumerate(col_ws):
            text = cells[j] if j < len(cells) else ""
            if not text:
                x += cw
                continue

            # 换行: \n → MTEXT \P
            text = text.replace("\\n", "\\P").replace("\n", "\\P")

            cx = x + cw / 2
            cy = y - rh / 2

            msp.add_mtext(text, dxfattribs={
                "layer": "评分表",
                "style": sty,
                "char_height": th,
                "insert": (cx, cy),
                "attachment_point": 5,        # 中中对齐
            })
            # 限制文字宽度，超出自动换行
            # ezdxf MTEXT width 通过 dxfattribs 不好设置，后处理
            x += cw

        y -= rh

    # 设置所有 MTEXT 的 width（单元格宽度 - 边距）
    for entity in msp:
        if entity.dxftype() == "MTEXT":
            if not entity.dxf.hasattr("width") or entity.dxf.width == 0:
                entity.dxf.width = 0          # 不限制宽度，靠换行符控制


# ── DXF → DWG 转换 ─────────────────────────────────────────

def dxf_to_dwg(dxf_path: str) -> str:
    """用 accoreconsole 将 DXF 转为 DWG。"""
    dwg_path = dxf_path.replace(".dxf", ".dwg")
    dwg_fwd = dwg_path.replace("\\", "/")
    work_dir = os.path.dirname(dxf_path)

    # LISP 脚本：SaveAs DWG 然后 Quit
    scr_path = os.path.join(work_dir, "_cvt.scr")
    with open(scr_path, "w", encoding="utf-8") as f:
        f.write('(setvar "FILEDIA" 0)\n')
        f.write('(setvar "CMDECHO" 0)\n')
        f.write(f'(command "_.SAVEAS" "2007" "{dwg_fwd}" "Y")\n')
        f.write('(command "_.QUIT" "Y")\n')

    dxf_win = dxf_path.replace("/", "\\")
    scr_win = scr_path.replace("/", "\\")
    cmd = f'"{ACCCORE}" /i "{dxf_win}" /b "{scr_win}"'
    log.info("DXF→DWG: %s → %s", dxf_win, dwg_fwd)

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=CONVERT_TIMEOUT,
                           cwd=work_dir)
    except subprocess.TimeoutExpired:
        raise RuntimeError("DXF→DWG conversion timed out")

    if os.path.isfile(dwg_path):
        return dwg_path

    log.error("DXF→DWG failed: rc=%d stderr=%s", r.returncode, r.stderr[:300])
    raise RuntimeError(f"DXF→DWG conversion failed (rc={r.returncode})")


# ── API ─────────────────────────────────────────────────────

@app.route("/api/export-dwg", methods=["POST"])
def export_dwg():
    data = request.get_json()
    if not data or "tables" not in data:
        return jsonify({"error": "Missing tables data"}), 400

    dxf_path = None
    try:
        dxf_path = generate_dxf(data)
        log.info("DXF created: %s", dxf_path)

        dwg_path = dxf_to_dwg(dxf_path)
        log.info("DWG ready: %s", dwg_path)

        # 读入内存后即可删除临时文件
        with open(dwg_path, "rb") as f:
            dwg_bytes = f.read()
        _safe_remove(dwg_path)

        filename = f"{data.get('project_code', 'export')}_评分表.dwg"
        return Response(dwg_bytes, mimetype="application/octet-stream",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{filename}"'})

    except Exception as e:
        log.error("Export failed: %s", e)
        return jsonify({"error": str(e)}), 500
    finally:
        _safe_remove(dxf_path)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok",
                    "accoreconsole": os.path.isfile(ACCCORE),
                    "ezdxf": ezdxf.__version__})


def _safe_remove(path):
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    log.info("DWG Export API starting on %s:%d", API_HOST, API_PORT)
    app.run(host=API_HOST, port=API_PORT, threaded=True)
