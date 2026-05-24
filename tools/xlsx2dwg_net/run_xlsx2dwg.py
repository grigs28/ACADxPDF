#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_xlsx2dwg.py — 编排器：xlsx → JSON → C# DLL → accoreconsole → DWG

支持 --threads N 并行转换（将 sections 分块后多线程运行）。

Usage:
    python run_xlsx2dwg.py <xlsx> [output_dir] [--sheet S] [--threads N]
"""
import os, sys, io, json, hashlib, shutil, subprocess, time, uuid, argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ── 路径常量 ──
ACCCORE = os.environ.get("ACAD_PATH", r"C:\opt\AutoCAD 2026\accoreconsole.exe")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DLL_DIR = os.path.join(SCRIPT_DIR, "XlsxToDwg")
DLL_PATH = os.path.join(DLL_DIR, "bin", "Release", "XlsxToDwg.dll")
DEFAULT_TEMPLATE = r"C:\opt\ACADxPDF\Template\mt.dwt"


def build_dll():
    """编译 C# DLL。1小时内编译过则跳过。"""
    if os.path.isfile(DLL_PATH) and time.time() - os.path.getmtime(DLL_PATH) < 3600:
        print(f"  DLL up-to-date: {DLL_PATH}")
        return DLL_PATH
    print("  Building DLL...")
    r = subprocess.run(
        ["dotnet", "build", "-c", "Release"],
        cwd=DLL_DIR,
        capture_output=True, text=True, timeout=60
    )
    if r.returncode != 0:
        print(f"BUILD FAILED:\n{r.stdout}\n{r.stderr}")
        return None
    print(f"  DLL built: {DLL_PATH}")
    return DLL_PATH


def _run_one(json_path, dll_path, output_dir, template, timeout=300):
    """
    单个 accoreconsole 实例：JSON → DWG。

    1. 创建临时工作目录 _work_<uuid>/
    2. 复制 JSON 到工作目录（避免中文路径问题）
    3. 生成 .scr 脚本
    4. 运行 accoreconsole
    5. 检查输出 DWG
    6. 清理工作目录

    Returns:
        dict: {'success': bool, 'dwg': str|None, 'elapsed': float, 'error': str}
    """
    uid = uuid.uuid4().hex[:8]
    work_dir = os.path.join(output_dir, f"_work_{uid}")
    os.makedirs(work_dir, exist_ok=True)

    # 复制 JSON 到工作目录（ASCII 安全路径）
    json_name = f"input_{uid}.json"
    json_work = os.path.join(work_dir, json_name)
    shutil.copy2(json_path, json_work)

    # 输出 DWG 路径
    dwg_name = f"output_{uid}.dwg"
    dwg_work = os.path.join(work_dir, dwg_name)

    # 生成脚本（路径全部用绝对路径 + 正斜杠）
    dll_fwd = os.path.abspath(dll_path).replace('\\', '/')
    json_fwd = os.path.abspath(json_work).replace('\\', '/')
    dwg_fwd = os.path.abspath(dwg_work).replace('\\', '/')
    tpl_abs = os.path.abspath(template)

    scr_name = f"run_{uid}.scr"
    scr_path = os.path.join(work_dir, scr_name)

    scr_lines = [
        '(setvar "FILEDIA" 0)',
        '(setvar "CMDECHO" 0)',
        '(setvar "SECURELOAD" 0)',
        '(setvar "EXPERT" 5)',
        f'(command "_.NETLOAD" "{dll_fwd}")',
        f'(command "XLSX2DWG" "{json_fwd}")',
        f'(command "_.SAVEAS" "2007" "{dwg_fwd}")',
        '(command "_.QUIT" "Y")',
    ]
    with open(scr_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(scr_lines))

    # 运行 accoreconsole（用字符串 cmd 格式，AutoCAD 2026 用 /s 不用 /b）
    cmd = f'"{ACCCORE}" /i "{tpl_abs}" /s "{os.path.abspath(scr_path)}"'
    t0 = time.time()
    error = ""
    try:
        r = subprocess.run(
            cmd, capture_output=True, timeout=timeout, cwd=work_dir,
        )
        elapsed = time.time() - t0
        if r.returncode != 0:
            error = f"RC={r.returncode}"
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        error = f"TIMEOUT after {timeout}s"
    except Exception as ex:
        elapsed = time.time() - t0
        error = str(ex)

    # 检查输出 DWG
    success = False
    dwg_final = None
    if os.path.isfile(dwg_work):
        # 移动到输出目录
        final_name = os.path.basename(dwg_work)
        dwg_final = os.path.join(output_dir, final_name)
        shutil.move(dwg_work, dwg_final)
        success = True

    # 清理工作目录
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:
        pass

    return {
        'success': success,
        'dwg': dwg_final,
        'elapsed': elapsed,
        'error': error,
    }


def convert_single(xlsx_path, output_dir, sheet_name, dll_path, template, timeout):
    """
    单文件转换：extract_to_json → build_dll → _run_one

    Returns:
        str|None: 生成的 DWG 路径，失败返回 None
    """
    from xlsx2json import extract_to_json

    output_dir = output_dir or str(Path(xlsx_path).parent)
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: xlsx → JSON
    print("[1/3] Extracting xlsx to JSON...")
    json_path = extract_to_json(xlsx_path, output_dir, sheet_name)
    if not json_path:
        print("ERROR: Failed to extract JSON")
        return None

    # Step 2: 确保 DLL 可用
    if dll_path:
        print(f"[2/3] Using provided DLL: {dll_path}")
    else:
        print("[2/3] Building DLL...")
        dll_path = build_dll()
    if not dll_path or not os.path.isfile(dll_path):
        print("ERROR: DLL not available")
        return None

    # Step 3: accoreconsole 执行
    print("[3/3] Running accoreconsole...")
    result = _run_one(json_path, dll_path, output_dir, template, timeout)
    elapsed = result['elapsed']

    if result['success']:
        sz = os.path.getsize(result['dwg'])
        print(f"SUCCESS: {result['dwg']} ({sz:,} bytes, {elapsed:.1f}s)")
        return result['dwg']
    else:
        print(f"FAILED: {result['error']} ({elapsed:.1f}s)")
        return None


def convert_parallel(xlsx_path, output_dir, sheet_name, dll_path, template, timeout, num_threads=4):
    """
    并行转换：
    1. 用 analyze_sheet 获取完整数据
    2. 将 sections 列表均分为 num_threads 个 chunk
    3. 每个 chunk 写为独立 JSON 文件
    4. ThreadPoolExecutor 并行调用 _run_one
    5. 收集结果，返回 DWG 列表

    Returns:
        list[str]: 生成的 DWG 路径列表
    """
    import openpyxl
    from xlsx2json import analyze_sheet

    output_dir = output_dir or str(Path(xlsx_path).parent)
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: 读取并分析 xlsx
    print("[1/4] Reading xlsx...")
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    sn = sheet_name or wb.sheetnames[0]
    ws = wb[sn]
    doc = analyze_sheet(ws)

    sections = doc.get('sections', [])
    if not sections:
        print("WARNING: No sections found")
        return []

    n = len(sections)
    actual_threads = min(num_threads, n)
    if actual_threads < 1:
        actual_threads = 1
    print(f"  {n} sections, splitting into {actual_threads} chunks")

    # Step 2: 分块
    chunk_size = (n + actual_threads - 1) // actual_threads
    chunks = []
    for i in range(0, n, chunk_size):
        chunk = sections[i:i + chunk_size]
        chunks.append(chunk)

    # Step 3: 每个 chunk 写独立 JSON
    json_paths = []
    for idx, chunk in enumerate(chunks):
        chunk_doc = dict(doc)
        chunk_doc['sections'] = chunk
        chunk_name = f"_chunk_{idx}.json"
        chunk_path = os.path.join(output_dir, chunk_name)
        with open(chunk_path, 'w', encoding='utf-8') as f:
            json.dump(chunk_doc, f, ensure_ascii=False, indent=2)
        json_paths.append(chunk_path)
        print(f"  Chunk {idx}: {len(chunk)} sections → {chunk_name}")

    # Step 4: 确保 DLL
    if dll_path:
        print(f"[2/4] Using provided DLL: {dll_path}")
    else:
        print("[2/4] Building DLL...")
        dll_path = build_dll()
    if not dll_path or not os.path.isfile(dll_path):
        print("ERROR: DLL not available")
        return []

    # Step 5: 并行执行
    print(f"[3/4] Running {len(chunks)} accoreconsole instances in parallel...")
    dwg_paths = []
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = {}
        for idx, jp in enumerate(json_paths):
            future = executor.submit(_run_one, jp, dll_path, output_dir, template, timeout)
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                if result['success']:
                    dwg_paths.append(result['dwg'])
                    print(f"  Chunk {idx}: OK ({result['elapsed']:.1f}s) → {result['dwg']}")
                else:
                    print(f"  Chunk {idx}: FAILED ({result['elapsed']:.1f}s) {result['error']}")
            except Exception as ex:
                print(f"  Chunk {idx}: EXCEPTION {ex}")

    # Step 4: 清理 chunk JSON 文件
    for jp in json_paths:
        try:
            os.remove(jp)
        except Exception:
            pass

    print(f"[4/4] Done: {len(dwg_paths)}/{len(chunks)} DWGs generated")
    return dwg_paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="xlsx → DWG via C# DLL + accoreconsole")
    parser.add_argument("xlsx", help="输入 xlsx 文件路径")
    parser.add_argument("output_dir", nargs="?", default=None, help="输出目录（默认与 xlsx 同目录）")
    parser.add_argument("--sheet", default=None, help="工作表名称")
    parser.add_argument("--threads", type=int, default=1, help="并行线程数（默认 1）")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE, help="DWT 模板路径")
    parser.add_argument("--timeout", type=int, default=300, help="超时秒数（默认 300）")
    parser.add_argument("--dll", default=None, help="预编译 DLL 路径")

    args = parser.parse_args()

    if args.threads > 1:
        convert_parallel(
            args.xlsx, args.output_dir, args.sheet, args.dll,
            args.template, args.timeout, args.threads
        )
    else:
        convert_single(
            args.xlsx, args.output_dir, args.sheet, args.dll,
            args.template, args.timeout
        )
