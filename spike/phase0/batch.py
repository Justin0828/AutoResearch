#!/usr/bin/env python3
"""Phase 0 spike：批量跑 trial 并汇总。"""
import argparse
import concurrent.futures as cf
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def one(task, i):
    out = HERE / "runs" / f"{task}-{i:02d}"
    if out.exists():
        shutil.rmtree(out)
    t0 = time.time()
    r = subprocess.run([sys.executable, str(HERE / "run_trial.py"),
                        "--task", task, "--out", str(out), "--timeout", "600"],
                       capture_output=True, text=True)
    v = subprocess.run([sys.executable, str(HERE / "validate.py"), str(out)],
                       capture_output=True, text=True)
    print(f"  {task}-{i:02d}  {time.time()-t0:5.0f}s  "
          f"{'PASS' if v.returncode == 0 else 'FAIL'}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", type=int, default=10)
    ap.add_argument("--distill", type=int, default=5)
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args()

    jobs = [("evidence", i) for i in range(1, a.evidence + 1)] + \
           [("distill", i) for i in range(1, a.distill + 1)]
    print(f"共 {len(jobs)} 个 trial，并发 {a.jobs}\n", flush=True)
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        list(ex.map(lambda j: one(*j), jobs))
    print(f"\n全部完成，耗时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
