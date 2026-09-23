"""初始化 State 仓库，从 spike/phase0/fixture 长出第一版真实 State。

从 fixture 迁移的只有研究内容本身（问题、A001、H001、H002），并按 v1.2 schema
改写：origin 移入 provenance.json、problem.md 变为 questions/Q001.md、假设补
validation、前提补 relied_on_by。

**不迁移** papers/P001–P003 与 dead-ends/D001：它们是为 spike 合成的（P 系列
文件自己写着「非真实论文」，D001 内嵌了一份没有实验记录的复现结果），放进
真实 State 会让后续证据判断建立在虚构文献上。
"""
import json

from . import frontmatter
from .config import CODE_ROOT
from .store import Store, today

FIXTURE = CODE_ROOT / "spike" / "phase0" / "fixture" / "state"

GITIGNORE = "*.tmp\n*.swp\n.DS_Store\n"

README = """# Research State

本仓库是 AutoResearch 的唯一真相来源（DESIGN.md §0.1 / §5.1）。
可以直接用编辑器修改；daemon 会以 `ar-human` 身份提交你的改动。

受保护路径（只能经工具或后端写）：project.md 的 mode、provenance.json、
evidence/、decisions/、candidates/、discussions/、handoffs/。
"""


def _seed(tx):
    d = today()
    tx.write("project.md", frontmatter.dump(
        {"id": "project", "type": "project",
         "title": "具身智能上下层接口下 action model 的精细操作能力",
         "mode": "discussion", "main_question": "Q001", "created": d},
        "# 研究项目\n\n具身智能的上下层接口划分。上层（GPT6 Astra 类）提供视觉知识、"
        "空间理解与任务拆解；下层是通用 action model。**不做上层**，聚焦 interface "
        "给定前提下 action model 的精细操作能力。\n"))

    # 研究内容取自 fixture 原文，只按 v1.2 schema 改写 frontmatter
    extra = {
        "Q001": {"src": "problem.md"},
        "A001": {"src": "assumptions/A001.md", "relied_on_by": ["Q001"]},
        "H001": {"src": "hypotheses/H001.md",
                 "validation": "文献核查 + 粒度×数据规模交叉实验（未排期）"},
        "H002": {"src": "hypotheses/H002.md",
                 "validation": "文献核查 + interface 语义消融实验（未排期）"},
    }
    for ident, add in extra.items():
        meta, body = frontmatter.parse((FIXTURE / add.pop("src")).read_text(encoding="utf-8"))
        meta.pop("origin", None)
        meta.pop("updated", None)
        meta["id"] = ident
        meta.update(add)
        meta["created"] = d
        order = ["id", "type", "status", "maturity", "confidence", "falsifier",
                 "validation", "relied_on_by", "evidence", "created"]
        meta = {k: meta[k] for k in order if k in meta}
        tx.write_obj(ident, meta, body)

    tx.write_obj("U001", {"id": "U001", "type": "uncertainty", "status": "open",
                          "importance": "high", "created": d},
                 """精细操作方向的 sim benchmark 与真机表现脱节是公认问题：任何基于纯仿真的
结论都要打这个折扣。（来源：DESIGN.md §2 推论 4。）
""")

    tx.write("provenance.json", json.dumps({
        "A001": {"origin": "human", "source": "phase0-fixture"},
        "H001": {"origin": "human", "source": "phase0-fixture"},
        "H002": {"origin": "human", "source": "phase0-fixture", "disputed": True,
                 "note": "fixture 记为 human；Phase 0 蒸馏试验多次指出讨论记录里是 AI 先提的。待人确认。"},
        "Q001": {"origin": "human", "source": "phase0-fixture"},
        "U001": {"origin": "ai", "source": "DESIGN.md §2"},
    }, ensure_ascii=False, indent=1) + "\n")
    tx.write(".gitignore", GITIGNORE)
    tx.write("README.md", README)


def init(cfg, seed=True):
    st = Store(cfg.state, cfg.lock)
    if (cfg.state / ".git").exists():
        return st, False
    cfg.state.mkdir(parents=True, exist_ok=True)
    st.git("init", "-q", "-b", "main")
    if seed:
        with st.tx("init: 从 Phase 0 fixture 建立 State（v1.2 schema）", actor="system") as tx:
            _seed(tx)
    return st, True
