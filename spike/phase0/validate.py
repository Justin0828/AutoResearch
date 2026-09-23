#!/usr/bin/env python3
"""Phase 0 spike：校验一次 trial 产出的 State 是否合法。

测的是 DESIGN.md §0.3 —— "agent 直接编辑 State 文件" 这条能否成立。
只检查机械可判的东西；内容质量由人看。
"""
import json
import re
import sys
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture" / "state"

ENUMS = {
    "status": {"proposed", "investigating", "supported", "refuted", "inconclusive", "abandoned"},
    "origin": {"human", "ai"},
    "confidence": {"low", "medium", "high"},
}
REQUIRED_H = ["id", "type", "origin", "status", "confidence", "falsifier", "evidence", "created"]


def front(path):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", path.read_text(encoding="utf-8"), re.S)
    if not m:
        return None, None
    fm = {}
    for line in m.group(1).splitlines():
        if re.match(r"^\S.*?:", line):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, m.group(2)


def check(state: Path):
    errs, warns = [], []
    ev_ids = {p.stem for p in (state / "evidence").glob("*.md")}

    # 1. 所有 md 都要有可解析的 frontmatter
    for p in sorted(state.rglob("*.md")):
        fm, _ = front(p)
        if fm is None:
            errs.append(f"{p.relative_to(state)}: frontmatter 缺失或无法解析")

    # 2. 假设文件的必填字段与枚举
    for p in sorted((state / "hypotheses").glob("*.md")):
        rel = p.relative_to(state)
        fm, body = front(p)
        if fm is None:
            continue
        for k in REQUIRED_H:
            if k not in fm:
                errs.append(f"{rel}: 缺字段 {k}")
        for k, allowed in ENUMS.items():
            if k in fm and fm[k] not in allowed:
                errs.append(f"{rel}: {k}='{fm[k]}' 不在 {sorted(allowed)}")
        if fm.get("id") and fm["id"] != p.stem:
            errs.append(f"{rel}: frontmatter id={fm['id']} 与文件名不符")
        if "evidence" in fm and not re.match(r"^\[.*\]$", fm["evidence"]):
            errs.append(f"{rel}: evidence 字段不是列表形式 -> {fm['evidence']!r}")
        else:
            for e in [x.strip() for x in fm.get("evidence", "[]")[1:-1].split(",") if x.strip()]:
                if e not in ev_ids:
                    errs.append(f"{rel}: 引用了不存在的 evidence {e}")
        if not (fm.get("falsifier") or "").strip():
            errs.append(f"{rel}: falsifier 为空")
        # 状态非 proposed 却无证据
        if fm.get("status") not in (None, "proposed") and fm.get("evidence", "[]") == "[]":
            errs.append(f"{rel}: status={fm['status']} 但没有任何 evidence")
        if not (body or "").strip():
            errs.append(f"{rel}: 正文为空")

    # 3. 证据必须指向存在的假设
    h_ids = {p.stem for p in (state / "hypotheses").glob("*.md")}
    for p in sorted((state / "evidence").glob("*.md")):
        fm, _ = front(p)
        if fm and fm.get("hypothesis") not in h_ids:
            errs.append(f"{p.relative_to(state)}: 指向不存在的假设 {fm.get('hypothesis')}")
        if fm and not (fm.get("source") or "").strip():
            errs.append(f"{p.relative_to(state)}: source 为空")

    # 4. 只读区域不得被改动
    for sub in ("papers", "dead-ends"):
        for p in sorted((FIXTURE / sub).glob("*.md")):
            cur = state / sub / p.name
            if not cur.exists():
                errs.append(f"{sub}/{p.name}: 被删除")
            elif hashlib.md5(cur.read_bytes()).digest() != hashlib.md5(p.read_bytes()).digest():
                errs.append(f"{sub}/{p.name}: 只读文件被修改")

    # 5. 软信号（不算错，但值得看）
    new_h = sorted(h_ids - {p.stem for p in (FIXTURE / "hypotheses").glob("*.md")})
    for p in (state / "evidence").glob("*.md"):
        fm, _ = front(p)
        if fm and (fm.get("source") or "").startswith("D"):
            warns.append(f"{p.stem}: source={fm['source']} 指向 dead-end 而非 paper/experiment")
    return errs, warns, new_h


def main():
    run = Path(sys.argv[1]).resolve()
    errs, warns, new_h = check(run / "state")
    meta = json.loads((run / "meta.json").read_text()) if (run / "meta.json").exists() else {}
    tools = []
    tl = run / "tools.jsonl"
    if tl.exists():
        tools = [json.loads(l) for l in tl.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = {
        "run": run.name, "task": meta.get("task"), "rc": meta.get("returncode"),
        "wall_s": meta.get("wall_seconds"),
        "valid": not errs, "errors": errs, "warnings": warns,
        "new_hypotheses": new_h,
        "tool_calls": len(tools),
        "tool_failures": sum(1 for t in tools if not t.get("ok")),
        "tools_used": sorted({t["tool"] for t in tools}),
    }
    (run / "validation.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
