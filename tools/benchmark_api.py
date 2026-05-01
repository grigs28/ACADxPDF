#!/usr/bin/env python3
"""ACADxPDF 多线程基准测试 — 通过 API 调用"""

import os
import sys
import time
import json
import glob
import urllib.request
import urllib.parse
import http.client
from io import BytesIO

API_HOST = "localhost"
API_PORT = 5557
DWG_DIR = "/opt/ACADxPDF/t3"

def get_dwg_files():
    return sorted(glob.glob(os.path.join(DWG_DIR, "*.dwg")))


def api_post_convert(dwgs, workers):
    """通过 HTTP multipart 上传文件到 /convert。"""
    boundary = "----BenchmarkBoundary" + str(int(time.time()))
    body = BytesIO()

    # 添加 workers 参数
    body.write(f"--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="workers"\r\n\r\n')
    body.write(f"{workers}\r\n".encode())

    # 添加文件
    for f in dwgs:
        fname = os.path.basename(f)
        with open(f, "rb") as fh:
            fdata = fh.read()
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="files"; filename="{fname}"\r\n'.encode())
        body.write(b"Content-Type: application/octet-stream\r\n\r\n")
        body.write(fdata)
        body.write(b"\r\n")

    body.write(f"--{boundary}--\r\n".encode())

    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=1800)
    conn.request("POST", "/convert",
                 body=body.getvalue(),
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data


def api_get_task(task_id):
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=30)
    conn.request("GET", f"/task/{task_id}")
    resp = conn.getresponse()
    data = json.loads(resp.read().decode())
    conn.close()
    return data


def run_benchmark(dwgs, workers):
    t0 = time.time()
    try:
        result = api_post_convert(dwgs, workers)
    except Exception as e:
        return {"error": str(e)}

    if "error" in result:
        return {"error": result["error"]}
    if "task_id" not in result:
        return {"error": f"无 task_id: {result}"}

    task_id = result["task_id"]

    for _ in range(600):
        time.sleep(1)
        data = api_get_task(task_id)
        if data.get("status") == "done":
            elapsed = time.time() - t0
            return {
                "task_id": task_id,
                "total_time": round(elapsed, 1),
                "ok_count": data.get("ok_count", 0),
                "total": data.get("total", 0),
                "total_pdfs": data.get("total_pdfs", 0),
                "workers": workers,
                "results": data.get("results", []),
                "zip_size_kb": data.get("zip_size_kb", 0),
            }

    return {"error": f"任务超时 (task_id={task_id})"}


def main():
    dwgs = get_dwg_files()
    n = len(dwgs)
    print(f"测试文件: {n} 个 DWG")
    for f in dwgs:
        size_kb = os.path.getsize(f) // 1024
        print(f"  {os.path.basename(f)} ({size_kb}KB)")
    print()

    thread_counts = [1, 2, 4, 6, 8, 12, 16]
    all_results = []

    for w in thread_counts:
        print(f"=== 测试 {w} 线程 ===")
        result = run_benchmark(dwgs, w)

        if "error" in result:
            print(f"  错误: {result['error']}")
            all_results.append({"workers": w, "error": result["error"]})
            continue

        ok = result["ok_count"]
        total = result["total"]
        pdfs = result["total_pdfs"]
        t = result["total_time"]
        avg = t / total if total > 0 else 0

        for fr in result.get("results", []):
            status = "OK" if fr.get("success") else "FAIL"
            print(f"  {status} {fr.get('elapsed',0):.1f}s {fr.get('pdf_count',0)}PDFs | {fr.get('file','?')[:40]}")

        print(f"  >>> 总耗时={t:.1f}s | 成功={ok}/{total} | PDF={pdfs} | 平均={avg:.1f}s/文件\n")

        all_results.append({
            "workers": w,
            "total_time": t,
            "ok_count": ok,
            "total": total,
            "total_pdfs": pdfs,
            "avg_time": round(avg, 1),
            "success_rate": f"{ok}/{total}",
            "zip_size_kb": result.get("zip_size_kb", 0),
            "results": result.get("results", []),
        })

    # 汇总
    print("=" * 90)
    print(f"{'线程':>4} | {'总耗时':>7} | {'成功':>5} | {'PDF':>4} | {'平均':>7} | {'加速比':>6} | {'ZIP(KB)':>8}")
    print("-" * 90)

    base_time = None
    for r in all_results:
        if "error" in r:
            print(f"{r['workers']:>4} | 错误: {r['error'][:50]}")
            continue
        if base_time is None:
            base_time = r["total_time"]
        speedup = base_time / r["total_time"] if r["total_time"] > 0 else 0
        print(f"{r['workers']:>4} | {r['total_time']:>6.1f}s | {r['ok_count']}/{r['total']} | {r['total_pdfs']:>4} | {r['avg_time']:>5.1f}s | {speedup:>5.2f}x | {r['zip_size_kb']:>8.0f}")

    # 保存 JSON
    report_path = "/opt/ACADxPDF/output/benchmark_results.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nJSON 结果: {report_path}")

    return all_results


if __name__ == "__main__":
    main()
