"""
PDF→DWG 单文件转换模块。

使用 acad.exe 打开模板 DWG + AutoLISP PDFIMPORT 命令实现。
每个 PDF 由独立的 acad.exe 进程处理。
"""

import os
import shutil
import subprocess
import time
import uuid


def _to_native_path(path: str) -> str:
    return path


def convert_one_pdf(pdf_path, output_dir, work_dir, acad_exe, timeout=300):
    """转换单个 PDF 为 DWG。

    Args:
        pdf_path:  输入 PDF 绝对路径
        output_dir: DWG 输出目录
        work_dir:  临时工作目录
        acad_exe:  acad.exe 完整路径
        timeout:   单文件超时秒数

    Returns:
        dict: {"ok", "elapsed", "dwg_size", "dwg_path", "error"}
    """
    from .converter import ACAD_TEMPLATE, _to_native_path as _tnp

    uid = uuid.uuid4().hex[:8]
    os.makedirs(work_dir, exist_ok=True)

    # 复制 PDF 到工作目录，用安全文件名避免中文路径问题
    safe_pdf = os.path.join(work_dir, f"_input_{uid}.pdf")
    shutil.copy2(pdf_path, safe_pdf)

    name = os.path.splitext(os.path.basename(pdf_path))[0]
    dwg_out = os.path.join(output_dir, f"{name}.dwg")

    pdf_win = _tnp(safe_pdf).replace("\\", "/")
    dwg_win = _tnp(dwg_out).replace("\\", "/")

    script = (
        '(setvar "FILEDIA" 0)\n'
        '(setvar "CMDECHO" 0)\n'
        '(setvar "CMDDIA" 0)\n'
        '(setvar "EXPERT" 5)\n'
        '(setvar "BACKGROUNDPLOT" 0)\n'
        '(setvar "PROXYNOTICE" 0)\n'
        '(setvar "DWGCHECK" 0)\n'
        '(setvar "RECOVERYMODE" 0)\n'
        '(setvar "FONTALT" "hztxt.shx")\n'
        '(setvar "ATTDIA" 0)\n'
        '(setvar "SIGWARN" 0)\n'
        '(setvar "STARTUP" 0)\n'
        '(setvar "NOMUTT" 1)\n'
        '(setvar "PDFIMPORTMODE" 0)\n'
        '(setvar "PDFIMPORTLAYERS" 0)\n'
        f'(command "-PDFIMPORT" "FILE" "{pdf_win}" "1" "0,0" "1" "0")\n'
        f'(command "_.SAVEAS" "2018" "{dwg_win}")\n'
        '(command "_.QUIT" "Y")\n'
    )

    scr_path = os.path.join(work_dir, "pdfimport.scr")
    with open(scr_path, "w", encoding="utf-8-sig") as f:
        f.write(script)

    t0 = time.time()

    # acad.exe 需要打开一个模板 DWG，否则没有活动文档，PDFIMPORT 无法执行
    cmd = [acad_exe, "/nologo"]
    if os.path.exists(ACAD_TEMPLATE):
        cmd.append(_tnp(ACAD_TEMPLATE))
    cmd += ["/b", _tnp(scr_path)]

    try:
        proc = subprocess.Popen(cmd)
    except FileNotFoundError:
        return {"ok": False, "elapsed": 0, "dwg_size": 0, "dwg_path": "",
                "error": f"acad.exe not found: {acad_exe}"}

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        elapsed = round(time.time() - t0, 1)
        return {"ok": False, "elapsed": elapsed, "dwg_size": 0, "dwg_path": "",
                "error": f"acad.exe timeout ({timeout}s)"}

    elapsed = round(time.time() - t0, 1)

    if os.path.isfile(dwg_out) and os.path.getsize(dwg_out) > 0:
        dwg_size = os.path.getsize(dwg_out)
        return {"ok": True, "elapsed": elapsed, "dwg_size": dwg_size,
                "dwg_path": dwg_out, "error": ""}
    else:
        return {"ok": False, "elapsed": elapsed, "dwg_size": 0, "dwg_path": "",
                "error": "no DWG output produced"}
