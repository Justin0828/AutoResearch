"""扁平 frontmatter 的解析与序列化（DESIGN.md §5.1）。

只支持 `key: value`，值为标量或行内列表 `[a, b]`，不支持嵌套——这是刻意的：
对 agent 和人手改都最不易出错，校验也能写得确定。零依赖。
"""
import re

_FRONT = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s?(.*)$")


class FrontmatterError(ValueError):
    pass


def _unquote(v):
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        return inner.replace('\\"', '"') if v[0] == '"' else inner
    return v


def _parse_value(raw):
    v = raw.strip()
    if v.startswith("[") and v.endswith("]"):
        body = v[1:-1].strip()
        if not body:
            return []
        return [_unquote(x.strip()) for x in body.split(",") if x.strip()]
    return _unquote(v)


def split(text):
    """返回 (frontmatter 文本 | None, 正文)。"""
    m = _FRONT.match(text)
    return (m.group(1), m.group(2)) if m else (None, text)


def parse(text):
    """返回 (dict | None, 正文)。frontmatter 缺失时 dict 为 None。

    缩进行、空行与 `#` 注释行被忽略；出现嵌套结构时抛 FrontmatterError。
    """
    fm, body = split(text)
    if fm is None:
        return None, text
    out = {}
    for line in fm.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            raise FrontmatterError(f"不支持嵌套/多行值：{line!r}")
        m = _KEY.match(line)
        if not m:
            raise FrontmatterError(f"无法解析的行：{line!r}")
        out[m.group(1)] = _parse_value(m.group(2))
    return out, body


def _fmt_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v)
    if s == "" or re.search(r"^[\s\[\"']|[\n]|:\s|\s#|\s$", s):
        return '"' + s.replace('"', '\\"').replace("\n", " ") + '"'
    return s


def _fmt(v):
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt_scalar(x) for x in v) + "]"
    return _fmt_scalar(v)


def dump(meta, body=""):
    lines = [f"{k}: {_fmt(v)}" for k, v in meta.items() if v is not None]
    body = body.lstrip("\n")
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body.rstrip() + "\n"
