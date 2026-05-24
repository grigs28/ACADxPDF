# acad2pdf/xlsx2dwg_worker.py
"""xlsx → DWG 转换（全专业），供 Worker 调用。"""

import logging
import os
import sys
import time

log = logging.getLogger("acad2pdf")

# xlsx2dwg 工具路径
_TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tools", "xlsx2dwg_net"
)
_DLL_PATH = os.path.join(_TOOLS_DIR, "XlsxToDwg", "bin", "Release", "XlsxToDwg.dll")
_DEFAULT_TEMPLATE = r"C:\opt\ACADxPDF\Template\A1.dwt"


def _ensure_tools():
    """确保 xlsx2json 和 run_xlsx2dwg 可导入。"""
    if _TOOLS_DIR not in sys.path:
        sys.path.insert(0, _TOOLS_DIR)


def _build_dll():
    """编译 C# DLL（1 小时内编译过则跳过）。"""
    import subprocess
    if os.path.isfile(_DLL_PATH) and time.time() - os.path.getmtime(_DLL_PATH) < 3600:
        return _DLL_PATH
    dll_dir = os.path.join(_TOOLS_DIR, "XlsxToDwg")
    r = subprocess.run(
        ["dotnet", "build", "-c", "Release"],
        cwd=dll_dir, capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        log.error("xlsx2dwg DLL build failed: %s", r.stderr)
        return None
    return _DLL_PATH


def convert_one_xlsx(xlsx_path, output_dir, sheets=None, dll_path=None,
                     template=None, timeout=300):
    """
    转换单个 xlsx 文件（可指定多个 sheet/专业）。

    Args:
        xlsx_path: 输入 xlsx 文件路径
        output_dir: 输出根目录
        sheets: 专业名称列表如 ["建筑","结构","水","暖","电"]，None=全部
        dll_path: 预编译 DLL 路径（None 则自动编译）
        template: DWT 模板路径
        timeout: accoreconsole 超时秒数

    Returns:
        {"ok": bool, "outputs": {sheet_name: dwg_path}, "error": str}
    """
    _ensure_tools()
    from xlsx2json import extract_to_json
    from run_xlsx2dwg import _run_one
    import openpyxl

    if not os.path.isfile(xlsx_path):
        return {"ok": False, "outputs": {}, "error": f"File not found: {xlsx_path}"}

    # 确定 sheet 列表
    if not sheets:
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        sheets = wb.sheetnames
        wb.close()

    # DLL
    effective_dll = dll_path or _build_dll()
    if not effective_dll or not os.path.isfile(effective_dll):
        return {"ok": False, "outputs": {}, "error": "DLL not available"}

    # 模板
    effective_tpl = template or _DEFAULT_TEMPLATE

    outputs = {}
    errors = []
    for sheet_name in sheets:
        try:
            sheet_dir = os.path.join(output_dir, sheet_name)
            os.makedirs(sheet_dir, exist_ok=True)

            # Step 1: xlsx → JSON
            json_path = extract_to_json(xlsx_path, sheet_dir, sheet_name)
            if not json_path:
                errors.append(f"{sheet_name}: extract failed")
                continue

            # Step 2: JSON → DWG via accoreconsole
            result = _run_one(json_path, effective_dll, sheet_dir,
                              effective_tpl, timeout)
            if result["success"]:
                outputs[sheet_name] = result["dwg"]
                log.info("xlsx2dwg: %s/%s OK (%.1fs)",
                         os.path.basename(xlsx_path), sheet_name, result["elapsed"])
            else:
                errors.append(f"{sheet_name}: {result['error']}")

            # 清理 JSON 中间文件
            try:
                os.remove(json_path)
            except OSError:
                pass

        except Exception as ex:
            errors.append(f"{sheet_name}: {ex}")
            log.error("xlsx2dwg %s/%s error: %s",
                      os.path.basename(xlsx_path), sheet_name, ex)

    ok = len(outputs) > 0
    error = "; ".join(errors) if errors else ""
    return {"ok": ok, "outputs": outputs, "error": error}
