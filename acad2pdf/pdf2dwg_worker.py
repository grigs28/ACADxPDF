"""
PDF→DWG 单文件转换模块。

使用 acad.exe /b + AutoLISP PDFIMPORT 命令实现。
每个 PDF 由独立的 acad.exe 进程处理。
"""

import os
import subprocess
import time
import uuid
import shutil


def convert_one_pdf(pdf_path, output_dir, work_dir, acad_exe, timeout=300):
    """转换单个 PDF 为 DWG。

    Args:
        pdf_path:  输入 PDF 绝对路径
        output_dir: DWG 输出目录
        work_dir:  临时工作目录（每个文件创建子目录）
        acad_exe:  acad.exe 完整路径
        timeout:   单文件超时秒数

    Returns:
        dict: {"ok", "elapsed", "dwg_size", "dwg_path", "error"}
    """
    name = os.path.splitext(os.path.basename(pdf_path))[0]
    uid = uuid.uuid4().hex[:8]
    my_work = os.path.join(work_dir, f"{name}_{uid}")
    os.makedirs(my_work, exist_ok=True)

    dwg_out = os.path.join(output_dir, f"{name}.dwg")
    pdf_lisp = pdf_path.replace("\\", "/")
    dwg_lisp = dwg_out.replace("\\", "/")

    script = (
        '(setvar "FILEDIA" 0)\n'
        '(setvar "CMDDIA" 0)\n'
        '(setvar "EXPERT" 5)\n'
        '(setvar "WHIPTHREAD" 3)\n'
        '(setvar "PDFIMPORTMODE" 0)\n'
        '(setvar "PDFIMPORTLAYERS" 0)\n'
        f'(command "-PDFIMPORT" "FILE" "{pdf_lisp}" "1" "0,0" "1" "0")\n'
        f'(command "_.SAVEAS" "2018" "{dwg_lisp}")\n'
        '(command "_.QUIT" "N")\n'
    )

    scr_path = os.path.join(my_work, "pdfimport.scr")
    with open(scr_path, "w", encoding="utf-8-sig") as f:
        f.write(script)

    t0 = time.time()
    try:
        proc = subprocess.Popen(
            [acad_exe, "/b", scr_path, "/nologo"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        shutil.rmtree(my_work, ignore_errors=True)
        return {"ok": False, "elapsed": 0, "dwg_size": 0, "dwg_path": "", "error": f"acad.exe not found: {acad_exe}"}

    ok = False
    while time.time() - t0 < timeout:
        if os.path.exists(dwg_out):
            size1 = os.path.getsize(dwg_out)
            time.sleep(2)
            if os.path.exists(dwg_out):
                size2 = os.path.getsize(dwg_out)
                if size1 == size2 and size1 > 0:
                    ok = True
                    break
        time.sleep(1)

    elapsed = round(time.time() - t0, 1)

    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    shutil.rmtree(my_work, ignore_errors=True)

    if ok:
        dwg_size = os.path.getsize(dwg_out)
        return {"ok": True, "elapsed": elapsed, "dwg_size": dwg_size, "dwg_path": dwg_out, "error": ""}
    else:
        if os.path.exists(dwg_out):
            os.remove(dwg_out)
        return {"ok": False, "elapsed": elapsed, "dwg_size": 0, "dwg_path": "", "error": "timeout or conversion failed"}
