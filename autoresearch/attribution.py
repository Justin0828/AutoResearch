"""署名线索的识别与中性化（DESIGN.md §5.2 judge 规则 2、§5.6 第 3 层）。

路径封读挡得住 provenance.json，挡不住对象正文里的“AI 在第 2 轮提议……研究者尚未认可”。
这里做两件事：neutralize() 把署名换成中性表述（judge briefing、候选确认写入正式对象时用）；
cues() 找出残留线索（校验器据此给警告）。屏蔽不可能完美，M8 的偏差指标兜底。
"""
import re

_WHO = r"(?:研究者|研究人员|人类|用户|我们|我|你|AI|ai|agent|Agent|模型|系统|Claude|助手)"
_VERB = (r"(?:认为|觉得|提出|提议|怀疑|倾向于|猜测|主张|认可|同意|反对|建议|确认|要求|指出|"
         r"坚持|质疑|担心|相信|判断|拍板|否定|接受|不认可|尚未认可|未认可)")

_PATTERNS = [
    # “AI 在第 2 轮提议”“研究者尚未认可”“我最早认为”：主语 + 不含标点的短间隔 + 立场动词
    (re.compile(rf"{_WHO}[^，。；、,.;:\n]{{0,12}}?({_VERB})"), r"有观点\1"),
    (re.compile(rf"(?:由|经)\s*{_WHO}\s*(提出|确认|提议)"), r"被\1"),
    (re.compile(r"\b(?:the\s+)?(?:researcher|human|user|AI|assistant|model)s?\s+"
                r"(proposed|suggested|thinks|believes|argued|insisted|doubts)\b", re.I), r"it was \1"),
    (re.compile(r"\b(?:proposed|suggested)\s+by\s+(?:the\s+)?(?:researcher|human|user|AI|assistant)\b",
                re.I), "proposed"),
    (re.compile(r"\b(?:human|AI|researcher)-(?:proposed|originated|suggested)\b", re.I), "proposed"),
    (re.compile(r"\borigin\s*[:：]\s*(?:human|ai|unclear)\b", re.I), ""),
    (re.compile(r"第\s*\d+(?:\s*[,，、]\s*\d+)*\s*轮"), "讨论中"),
    (re.compile(r"（经候选 C\d+ 确认，出自讨论 DS\d+ 第 [\d, ]+ 轮。）"), ""),
    (re.compile(r"（来由见候选 C\d+。?）"), ""),
]


def neutralize(text):
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text or "")
    return text


def cues(text):
    """返回文本里的署名线索片段（去重，最多 5 条）。"""
    out = []
    for pat, _ in _PATTERNS[:5]:
        for m in pat.finditer(text or ""):
            s = m.group(0).strip()
            if s and s not in out:
                out.append(s)
    return out[:5]
