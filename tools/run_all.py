# -*- coding: utf-8 -*-
"""一键跑完全部验收测试，给出一张总结表。

用法：
    python tools/run_all.py            # 常规全量（几分钟）
    python tools/run_all.py --quick    # 更快：更少种子/更短天数

任一子测试非零退出、或输出里出现 FAIL / ✗ / Traceback，即判该套失败并打印其尾部输出。
退出码 0 = 全部通过。
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
FAIL_MARKERS = ("FAIL", "✗", "Traceback", "存在未通过的检查项")


def suites(quick):
    seeds, days = (3, 240) if quick else (6, 360)
    return [
        ("规格一致性", ["tools/validate.py"]),
        ("集成：整跑=分段续跑", ["tools/play_test.py"]),
        ("确定性：同种子复现", ["tools/determinism_test.py", "--seeds", str(seeds), "--days", str(days)]),
        ("P2 题材扩展验收", ["tools/genre_test.py"]),
        ("P3 输出交互验收", ["tools/p3_test.py"]),
        ("叙事后端（本机/云端）", ["tools/llm_test.py"]),
        ("状态锚定验收", ["tools/state_test.py"]),
        ("设定三态与网文导入", ["tools/canon_test.py"]),
        ("聚落营造：住房可增长", ["tools/housing_test.py"]),
        ("平台端到端（HTTP）", ["tools/platform_test.py", "--offline"]),
        ("自主行为", ["tools/autonomy_test.py"]),
        ("存档分支", ["tools/branches_test.py"]),
        ("历史完整性", ["tools/verify_history.py", "--no-replay"]),
    ]


def main():
    ap = argparse.ArgumentParser(description="一键跑完《灵途自生》全部验收测试")
    ap.add_argument("--quick", action="store_true", help="更快：更少种子/更短天数")
    a = ap.parse_args()

    print("=" * 74)
    print("灵途自生 · 全量验收测试" + ("（quick）" if a.quick else ""))
    print("=" * 74)
    results = []
    for label, argv in suites(a.quick):
        started = time.time()
        proc = subprocess.run([PY] + argv, cwd=ROOT, capture_output=True, text=True, errors="replace")
        out = (proc.stdout or "") + (proc.stderr or "")
        ok = proc.returncode == 0 and not any(m in out for m in FAIL_MARKERS)
        results.append((label, ok, time.time() - started, out))
        print(f"  {'✔ 通过' if ok else '✘ 失败'}  {label:<22} {time.time() - started:5.1f}s", flush=True)
    print("-" * 74)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 套：通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for label, _, _, out in failed:
        print(f"\n── 失败：{label} ──")
        print("\n".join(out.strip().splitlines()[-15:]))
    print("=" * 74)
    if failed:
        print("结论：存在未通过的测试，见上。")
        return 1
    print("结论：全部通过。P0–P3 验收 + 加固项均成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
