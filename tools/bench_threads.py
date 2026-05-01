#!/usr/bin/env python3
"""ACADxPDF 多线程基准测试 — 1~32 线程，自动停止"""

import os
import sys
import time
import shutil
import glob
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from acad2pdf.converter import convert_dwg

BENCH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench32")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "bench")
THREAD_COUNTS = [1, 2, 4, 6, 8, 12, 16, 20, 24, 32]
STOP_THRESHOLD = 0.05  # 加速比提升 <5% 则停止


def get_rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 0


def process_dwg(dwg_path, output_dir):
    t0 = time.time()
    r = convert_dwg(dwg_path, output_dir)
    elapsed = time.time() - t0
    n_pdf = len(r.borders) if r.borders else (1 if r.success else 0)
    return {
        "name": os.path.basename(dwg_path),
        "ok": r.success,
        "pdfs": n_pdf,
        "elapsed": round(elapsed, 1),
        "error": r.error,
    }


def run_round(dwgs, workers):
    shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    rss_samples = [get_rss_mb()]
    t_start = time.time()
    results = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(process_dwg, d, OUTPUT_DIR) for d in dwgs]
        for ft in futures:
            r = ft.result()
            results.append(r)
            rss_samples.append(get_rss_mb())

    total_time = round(time.time() - t_start, 1)
    ok_count = sum(1 for r in results if r["ok"])
    total_pdfs = sum(r["pdfs"] for r in results)
    peak_rss = max(rss_samples)

    for r in results:
        s = "OK" if r["ok"] else "FAIL"
        print(f"  {s} {r['elapsed']}s {r['pdfs']}PDFs | {r['name'][:40]}")

    return {
        "workers": workers,
        "total_time": total_time,
        "ok_count": ok_count,
        "total": len(dwgs),
        "total_pdfs": total_pdfs,
        "peak_rss_mb": round(peak_rss, 0),
        "results": results,
    }


def main():
    dwgs = sorted(glob.glob(os.path.join(BENCH_DIR, "*.dwg")))
    n = len(dwgs)
    print(f"ACADxPDF 多线程基准测试")
    print(f"日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"测试文件: {n} 个 DWG")
    for d in dwgs:
        kb = os.path.getsize(d) // 1024
        print(f"  {os.path.basename(d)} ({kb}KB)")
    print()

    all_results = []
    prev_speedup = 0

    for w in THREAD_COUNTS:
        if w > n:
            break

        print(f"{'='*70}")
        print(f"W={w} — {w} 个 accoreconsole 并行消费 {n} 个 DWG")
        print(f"{'='*70}")

        r = run_round(dwgs, w)
        print(f"\n>>> W={w} 总耗时: {r['total_time']}s | {r['ok_count']}/{r['total']} OK | {r['total_pdfs']} PDFs | RSS: {r['peak_rss_mb']:.0f}MB\n")

        if all_results:
            base_time = all_results[0]["total_time"]
            speedup = base_time / r["total_time"] if r["total_time"] > 0 else 0
            r["speedup"] = round(speedup, 2)
            improvement = (speedup - prev_speedup) / prev_speedup if prev_speedup > 0 else 1
            print(f"    加速比: {speedup:.2f}x (vs 1线程), 提升: {improvement*100:.1f}% (vs 上轮)")
            if prev_speedup > 0 and improvement < STOP_THRESHOLD:
                print(f"\n    *** 加速比提升 <{STOP_THRESHOLD*100:.0f}%，停止测试 ***\n")
                all_results.append(r)
                break
            prev_speedup = speedup
        else:
            r["speedup"] = 1.0
            prev_speedup = 1.0

        all_results.append(r)

    # 汇总表
    print(f"\n{'='*70}")
    print(f"汇总")
    print(f"{'='*70}")
    print(f"{'W':>4} | {'总耗时':>7} | {'成功':>5} | {'PDF':>4} | {'RSS':>6} | {'加速比':>6} | 平均(s/文件)")
    print("-" * 70)
    for r in all_results:
        avg = r["total_time"] / r["total"] if r["total"] > 0 else 0
        print(f"{r['workers']:>4} | {r['total_time']:>6.1f}s | {r['ok_count']}/{r['total']} | {r['total_pdfs']:>4} | {r['peak_rss_mb']:>5.0f}MB | {r['speedup']:>5.2f}x | {avg:.1f}")

    # 生成报告
    report = generate_report(all_results, n)
    report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "docs", "线程性能测试报告.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已保存: {report_path}")


def generate_report(results, n_files):
    lines = []
    lines.append("# ACADxPDF 线程性能测试报告\n")
    lines.append(f"**测试日期：** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**测试方法：** 直接调用 `convert_dwg()`，ThreadPoolExecutor 队列消费")
    lines.append(f"**测试文件：** {n_files} 个 DWG（bench32 目录）\n")

    # 汇总表
    lines.append("## 测试结果汇总\n")
    lines.append(f"| 线程数 | 总耗时(s) | 成功率 | PDF数 | 峰值RSS(MB) | 加速比 | 平均(s/文件) |")
    lines.append(f"|--------|----------|--------|-------|------------|--------|-------------|")
    for r in results:
        avg = r["total_time"] / r["total"] if r["total"] > 0 else 0
        lines.append(f"| {r['workers']} | {r['total_time']:.1f} | {r['ok_count']}/{r['total']} | {r['total_pdfs']} | {r['peak_rss_mb']:.0f} | {r['speedup']:.2f}x | {avg:.1f} |")

    # 结论
    lines.append("\n## 分析\n")
    best = max(results, key=lambda x: x["speedup"])
    lines.append(f"- **最优线程数：** W={best['workers']}（加速比 {best['speedup']:.2f}x）")
    lines.append(f"- **单线程基准：** {results[0]['total_time']:.1f}s")
    lines.append(f"- **最快总耗时：** {best['total_time']:.1f}s（W={best['workers']}）")
    if len(results) > 1:
        last = results[-1]
        lines.append(f"- **测试终止点：** W={last['workers']}，加速比不再显著提升")
    lines.append(f"- **内存开销：** 1线程 {results[0]['peak_rss_mb']:.0f}MB → 最优 {best['peak_rss_mb']:.0f}MB（+{best['peak_rss_mb']-results[0]['peak_rss_mb']:.0f}MB）")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
