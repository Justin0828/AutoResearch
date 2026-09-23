"""讨论的持久化（M1.9，DESIGN.md §5.1 规则 5）。

transcript.md 只由后端追加；轮次以注释行分隔，正文里写任何标题都不会切错。
summary.md 只由 update_discussion_summary 写，covers_through 标明摘要覆盖到第几轮。
"""
import re

from . import frontmatter
from .store import now, today

_TURN = re.compile(r"^<!-- turn (\d+) (human|ai) (\S+) -->$", re.M)
ROLE_TITLE = {"human": "人", "ai": "AI"}


def rel_transcript(ds):
    return f"discussions/{ds}/transcript.md"


def rel_summary(ds):
    return f"discussions/{ds}/summary.md"


def create(store, title, actor="human"):
    with store.tx("discussion: 新建讨论", actor=actor) as tx:
        ds = store.next_id("DS", "discussions")
        tx.write(rel_transcript(ds), frontmatter.dump(
            {"id": ds, "type": "discussion", "title": title or "未命名讨论",
             "status": "open", "created": today()},
            f"# {title or '未命名讨论'}\n"))
        tx.note = ds
    return ds


def list_all(store):
    out = []
    d = store.state / "discussions"
    for p in sorted(d.glob("DS*/transcript.md")) if d.is_dir() else []:
        meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
        ts = parse_turns(body)
        out.append({**(meta or {}), "turns": len(ts),
                    "last_ts": ts[-1]["ts"] if ts else None,
                    "awaiting_reply": bool(ts) and ts[-1]["role"] == "human",
                    "undistilled_human": undistilled_human(store, meta["id"], ts)})
    return out


def parse_turns(body):
    marks = list(_TURN.finditer(body or ""))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        chunk = body[m.end():end].strip("\n")
        chunk = re.sub(r"^### (人|AI)\n\n?", "", chunk, count=1)
        out.append({"n": int(m.group(1)), "role": m.group(2), "ts": m.group(3),
                    "text": chunk.strip()})
    return out


def read(store, ds):
    text = store.read(rel_transcript(ds))
    if text is None:
        raise KeyError(ds)
    meta, body = frontmatter.parse(text)
    return meta, parse_turns(body)


def append_turn(store, ds, role, text, actor=None, task=None):
    if role not in ROLE_TITLE:
        raise ValueError(role)
    text = (text or "").strip()
    if not text:
        raise ValueError("发言不能为空")
    with store.tx(f"discussion {ds}: {ROLE_TITLE[role]}发言", actor=actor or
                  ("human" if role == "human" else "agent"), task=task) as tx:
        meta, turns = read(store, ds)
        if meta.get("status") != "open":
            raise ValueError(f"{ds} 已关闭")
        n = (turns[-1]["n"] + 1) if turns else 1
        tx.append(rel_transcript(ds),
                  f"\n<!-- turn {n} {role} {now()} -->\n### {ROLE_TITLE[role]}\n\n{text}\n")
        tx.note = f"第 {n} 轮"
    return n


def set_status(store, ds, status):
    with store.tx(f"discussion {ds}: {status}", actor="human") as tx:
        meta, body = frontmatter.parse(store.read(rel_transcript(ds)))
        meta["status"] = status
        tx.write(rel_transcript(ds), frontmatter.dump(meta, body))


def summary(store, ds):
    t = store.read(rel_summary(ds))
    if not t:
        return {"covers_through": 0}, ""
    meta, body = frontmatter.parse(t)
    return meta or {"covers_through": 0}, body


def covers_through(store, ds):
    try:
        return int(summary(store, ds)[0].get("covers_through", 0))
    except (TypeError, ValueError):
        return 0


def undistilled_human(store, ds, turns=None):
    if turns is None:
        turns = read(store, ds)[1]
    c = covers_through(store, ds)
    return sum(1 for t in turns if t["n"] > c and t["role"] == "human")


def write_summary(store, ds, text, through, task=None):
    """update_discussion_summary 的实现。through 必须是已存在的轮次。"""
    _, turns = read(store, ds)
    last = turns[-1]["n"] if turns else 0
    if not (0 < through <= last):
        raise ValueError(f"covers_through={through} 越界：{ds} 当前共 {last} 轮")
    if through < covers_through(store, ds):
        raise ValueError(f"covers_through 不能后退（当前 {covers_through(store, ds)}）")
    if not text.strip():
        raise ValueError("摘要不能为空")
    with store.tx(f"discussion {ds}: 更新摘要至第 {through} 轮", actor="agent", task=task) as tx:
        tx.write(rel_summary(ds), frontmatter.dump(
            {"id": f"{ds}-summary", "type": "discussion_summary", "discussion": ds,
             "covers_through": through, "created": today(), "updated": now()},
            text.strip() + "\n"))
