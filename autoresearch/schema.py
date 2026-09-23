"""Research State schema 的单一机器可读定义（说明见 DESIGN.md §5.1）。

由 spike/phase0/validate.py 长成：Phase 0 只查假设与证据，这里覆盖全部对象，
并加入 Phase 0 发现的三条规则——origin 不进对象文件、Assumption/Hypothesis
按角色区分（relied_on_by / validation 必填）、DeadEnd 只引用不内嵌。
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter


@dataclass(frozen=True)
class Kind:
    type: str
    dir: str            # 相对 State 根；"" 表示根目录单文件
    prefix: str
    required: tuple = ()
    enums: dict = field(default_factory=dict)
    nonempty: tuple = ()          # 必须非空的字段
    refs: dict = field(default_factory=dict)  # 字段 -> 允许的 id 前缀
    tool_only: bool = False       # agent 不得用 Write/Edit 直接改


KINDS = {k.type: k for k in [
    Kind("question", "questions", "Q", ("maturity",),
         {"maturity": {"vague", "scoped", "formalized"}}),
    Kind("assumption", "assumptions", "A", ("status", "relied_on_by"),
         {"status": {"unexamined", "examined", "promoted", "retired", "invalidated"},
          "fragile": {"true", "false"}},
         nonempty=("relied_on_by",),
         refs={"relied_on_by": ("Q", "A", "H", "I", "U", "IN"), "promoted_to": ("H",),
               "invalidated_by": ("E", "DEC"), "derived_from": ("IN",)}),
    Kind("hypothesis", "hypotheses", "H",
         ("status", "confidence", "falsifier", "validation", "evidence"),
         {"status": {"proposed", "investigating", "supported", "refuted",
                     "inconclusive", "abandoned"},
          "confidence": {"low", "medium", "high"}},
         nonempty=("falsifier", "validation"),
         refs={"evidence": ("E",), "promoted_from": ("A",)}),
    Kind("evidence", "evidence", "E", ("hypothesis", "stance", "strength", "source"),
         {"stance": {"support", "contradict", "neutral"},
          "strength": {"weak", "moderate", "strong"}},
         nonempty=("source",),
         refs={"hypothesis": ("H",), "source": ("P", "X")}, tool_only=True),
    Kind("paper", "papers", "P", ("title",), nonempty=("title",)),
    Kind("dead-end", "dead-ends", "D", ("status", "closed_by"),
         {"status": {"closed", "reopened"}},
         nonempty=("closed_by",), refs={"closed_by": ("E", "X")}),
    # 理解（DESIGN.md M1 Insight）：研究的产出，可以是描述性的直觉，不要求可证伪，但必须说出根基
    Kind("insight", "insights", "IN", ("status", "firmness"),
         {"status": {"active", "superseded", "abandoned"},
          "firmness": {"hunch", "working", "settled"}},
         refs={"basis": ("Q", "A", "H", "E", "P", "X", "U", "IN", "DS", "D"),
               "informs": ("Q", "A", "H", "U", "IN"), "superseded_by": ("IN",)}),
    Kind("uncertainty", "uncertainties", "U", ("status", "importance"),
         {"status": {"open", "reduced", "resolved"},
          "importance": {"low", "medium", "high"}}),
    Kind("decision", "decisions", "DEC", ("kind", "refs"),
         {"kind": {"research", "curation", "mode", "handoff"}}, tool_only=True),
    Kind("candidate", "candidates", "C", ("kind", "status", "origin", "source", "turns"),
         {"kind": {"assumption", "hypothesis", "question", "uncertainty", "insight"},
          "status": {"pending", "accepted", "rejected", "superseded"},
          "origin": {"human", "ai", "unclear"}},
         refs={"source": ("DS",)}, tool_only=True),
    Kind("handoff", "handoffs", "HO", ("shift", "reason", "started", "ended"),
         {"reason": {"normal", "quota_5h", "quota_7d", "cutoff", "crash", "manual"}},
         tool_only=True),
    # 推翻的传播（DESIGN.md M5.6b）：只由对账函数生成，人经前端处理
    Kind("review", "reviews", "R", ("trigger", "event", "target", "status", "depth"),
         {"event": {"hypothesis_refuted", "assumption_invalidated", "insight_withdrawn"},
          "status": {"open", "resolved", "dismissed"}},
         refs={"trigger": ("A", "H", "IN"), "target": ("Q", "A", "H", "U", "C", "I", "IN")},
         tool_only=True),
]}

BY_PREFIX = {k.prefix: k for k in KINDS.values()}

# 候选对象按 kind 需附带的目标字段（成为正式对象时必需）
CANDIDATE_FIELDS = {
    "assumption": ("relied_on_by",),
    "hypothesis": ("falsifier", "validation"),
    "question": ("maturity",),
    "uncertainty": ("importance",),
    "insight": ("firmness",),          # basis / basis_note 至少其一，单独检查
}

PROJECT_MODES = {"discussion", "incubation", "validation"}
ORIGINS = {"human", "ai"}
COMMON = ("id", "type", "created")

# agent 不得直接 Write/Edit 的路径（DESIGN.md §5.1「受保护路径」）
PROTECTED = ("project.md", "provenance.json", "evidence/", "decisions/",
             "candidates/", "discussions/", "handoffs/", "reviews/")

_ID = re.compile(r"^([A-Z]+)(\d{3,})$")


def split_id(ident):
    m = _ID.match(ident or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


def kind_of_id(ident):
    p, _ = split_id(ident)
    return BY_PREFIX.get(p)


def path_of(state, ident):
    k = kind_of_id(ident)
    return Path(state) / k.dir / f"{ident}.md" if k else None


def is_protected(rel):
    rel = str(rel).lstrip("./")
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in PROTECTED)


def all_ids(state):
    state = Path(state)
    ids = set()
    for k in KINDS.values():
        d = state / k.dir
        if d.is_dir():
            ids.update(p.stem for p in d.glob(f"{k.prefix}*.md") if split_id(p.stem)[0] == k.prefix)
    ids.update(p.name for p in (state / "discussions").glob("DS*") if p.is_dir())
    ids.update(p.stem for p in (state / "experiments").glob("X*.md"))
    return ids


def _as_list(v):
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


def check_object(meta, kind, ids, rel):
    """校验单个对象，返回错误列表。ids 为 State 中现存的全部 id。"""
    errs = []
    for f in COMMON + kind.required:
        if f not in meta:
            errs.append(f"{rel}: 缺字段 {f}")
    if meta.get("type") not in (None, kind.type):
        errs.append(f"{rel}: type={meta.get('type')} 应为 {kind.type}")
    for f, allowed in kind.enums.items():
        if f in meta and meta[f] not in allowed:
            errs.append(f"{rel}: {f}='{meta[f]}' 不在 {sorted(allowed)}")
    for f in kind.nonempty:
        v = meta.get(f)
        if f in meta and (v == [] or not str(v).strip()):
            errs.append(f"{rel}: {f} 不能为空")
    for f, prefixes in kind.refs.items():
        for r in _as_list(meta.get(f)):
            p, _ = split_id(r)
            if p not in prefixes:
                errs.append(f"{rel}: {f} 中的 '{r}' 不是 {'/'.join(prefixes)} 类 id")
            elif r not in ids:
                errs.append(f"{rel}: {f} 引用了不存在的 {r}")
    if "origin" in meta and kind.type != "candidate":
        errs.append(f"{rel}: frontmatter 含 origin——origin 只能存在 provenance.json（§5.1 规则 1）")

    if kind.type == "assumption" and meta.get("status") == "invalidated" \
            and not _as_list(meta.get("invalidated_by")):
        errs.append(f"{rel}: status=invalidated 但没有 invalidated_by（证据或人的推翻决定）")
    if kind.type == "insight" or (kind.type == "candidate" and meta.get("kind") == "insight"):
        if not _as_list(meta.get("basis")) and not str(meta.get("basis_note") or "").strip():
            errs.append(f"{rel}: 理解必须说出根基（basis 或 basis_note 至少其一）")
    if kind.type == "insight" and meta.get("status") == "superseded" and not meta.get("superseded_by"):
        errs.append(f"{rel}: status=superseded 但没有 superseded_by")
    if kind.type == "hypothesis":
        if meta.get("status") not in (None, "proposed") and not _as_list(meta.get("evidence")):
            errs.append(f"{rel}: status={meta['status']} 但没有任何 evidence")
    if kind.type == "candidate":
        for f in CANDIDATE_FIELDS.get(meta.get("kind"), ()):
            if not meta.get(f):
                errs.append(f"{rel}: kind={meta.get('kind')} 的候选必须带 {f}")
        if meta.get("origin") == "unclear" and not meta.get("origin_note"):
            errs.append(f"{rel}: origin=unclear 时必须写 origin_note 说明冲突")
        if meta.get("status") == "accepted" and not meta.get("promoted_to"):
            errs.append(f"{rel}: accepted 的候选缺 promoted_to")
    return errs


def validate_repo(state):
    """校验整个 State 仓库。返回 (errors, warnings)。"""
    state = Path(state)
    errs, warns = [], []
    ids = all_ids(state)

    pj = state / "project.md"
    if not pj.exists():
        errs.append("project.md 缺失")
    else:
        meta, _ = _safe_parse(pj, errs, state)
        if meta is not None:
            for f in COMMON + ("title", "mode", "main_question"):
                if f not in meta:
                    errs.append(f"project.md: 缺字段 {f}")
            if meta.get("mode") not in PROJECT_MODES:
                errs.append(f"project.md: mode='{meta.get('mode')}' 不在 {sorted(PROJECT_MODES)}")
            if meta.get("main_question") and meta["main_question"] not in ids:
                errs.append(f"project.md: main_question {meta['main_question']} 不存在")

    for kind in KINDS.values():
        d = state / kind.dir
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            rel = p.relative_to(state)
            meta, body = _safe_parse(p, errs, state)
            if meta is None:
                continue
            if meta.get("id") != p.stem:
                errs.append(f"{rel}: frontmatter id={meta.get('id')} 与文件名不符")
            errs.extend(check_object(meta, kind, ids, rel))
            if kind.type not in ("decision", "handoff") and not (body or "").strip():
                errs.append(f"{rel}: 正文为空")

    for d in sorted((state / "discussions").glob("DS*")):
        t = d / "transcript.md"
        if not t.exists():
            errs.append(f"discussions/{d.name}: 缺 transcript.md")
            continue
        meta, _ = _safe_parse(t, errs, state)
        if meta and meta.get("status") not in ("open", "closed"):
            errs.append(f"discussions/{d.name}/transcript.md: status 非 open/closed")

    prov_path = state / "provenance.json"
    if prov_path.exists():
        try:
            prov = json.loads(prov_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errs.append(f"provenance.json: 不是合法 JSON（{e}）")
            prov = {}
        for ident, rec in prov.items():
            if ident not in ids:
                warns.append(f"provenance.json: {ident} 对应的对象不存在")
            if (rec or {}).get("origin") not in ORIGINS:
                errs.append(f"provenance.json: {ident} 的 origin 不在 {sorted(ORIGINS)}")
        for ident in ids:
            k = kind_of_id(ident)
            if k and k.type in ("assumption", "hypothesis", "question", "uncertainty", "insight") \
                    and ident not in prov:
                warns.append(f"{ident}: provenance.json 中没有 origin 记录")
    return errs, warns


def _safe_parse(p, errs, state):
    try:
        meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
    except frontmatter.FrontmatterError as e:
        errs.append(f"{p.relative_to(state)}: {e}")
        return None, None
    if meta is None:
        errs.append(f"{p.relative_to(state)}: frontmatter 缺失或无法解析")
    return meta, body
