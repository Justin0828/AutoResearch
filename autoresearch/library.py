"""M4 文献库：核实登记所需的元数据、全文获取与段落锚点、引文校验（DESIGN.md §5.5）。

全文不进 State 的 git，放在 $AR_ROOT/library/P###/：
  source.html | source.pdf   原件
  fulltext.md                给 agent 读的正文，每段带稳定锚点 [s3.2-p4]
  anchors.json               {锚点: 原文}，record_evidence 用它校验 quote
  meta.json                  来源、sha256、转换方式

网络全部走宿主默认路由（sing-box，spike/phase2 实测 arXiv 可用）。测试时设
AR_NET_FIXTURES 指向一个目录，所有请求改读其中的文件，不碰网络。
"""
import hashlib
import html
import json
import os
import re
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

UA = "AutoResearch/0.2 (literature assistant)"
ARXIV_ID = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(v\d+)?$", re.I)
DOI = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/\S+)$", re.I)
MIN_QUOTE = 15          # 归一化后少于这么多字符的“引文”不算引文
MAX_QUOTE = 400


class NetError(RuntimeError):
    pass


# ---------------------------------------------------------------- 网络

_arxiv_lock = threading.Lock()
_arxiv_last = [0.0]


def _fixture_name(url):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", re.sub(r"^https?://", "", url))[:200]


def http_get(url, timeout=60):
    fx = os.environ.get("AR_NET_FIXTURES")
    if fx:
        name = _fixture_name(url)
        p = Path(fx) / name
        if not p.exists():      # 退而取最长的前缀匹配（检索 URL 带查询串，夹具按前缀给）
            cands = [f for f in Path(fx).iterdir() if name.startswith(f.name)]
            if not cands:
                raise NetError(f"离线测试缺少夹具：{name}")
            p = max(cands, key=lambda f: len(f.name))
        return p.read_bytes()
    if "arxiv.org" in url:          # arXiv API 要求两次请求间隔 ≥3s
        with _arxiv_lock:
            wait = 3.0 - (time.time() - _arxiv_last[0])
            if wait > 0:
                time.sleep(wait)
            _arxiv_last[0] = time.time()
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 503) and attempt < 2:
                time.sleep(4 * (attempt + 1))
                continue
            raise NetError(f"HTTP {e.code}：{url}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt < 2:
                time.sleep(2)
                continue
    raise NetError(f"请求失败：{url}（{last}）")


# ---------------------------------------------------------------- 元数据

def norm_arxiv(s):
    m = ARXIV_ID.match((s or "").strip())
    return m.group(1) if m else None


def norm_doi(s):
    m = DOI.match((s or "").strip())
    return m.group(1).rstrip(".").lower() if m else None


_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _entries(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.findall("a:entry", _ATOM):
        eid = (e.findtext("a:id", "", _ATOM) or "").strip()
        m = re.search(r"arxiv\.org/abs/(.+?)(v\d+)?$", eid)
        if not m:
            continue
        title = " ".join((e.findtext("a:title", "", _ATOM) or "").split())
        if not title or title.lower() == "error":
            continue
        out.append({
            "arxiv": m.group(1),
            "title": title,
            "authors": [" ".join((a.findtext("a:name", "", _ATOM) or "").split())
                        for a in e.findall("a:author", _ATOM)],
            "year": (e.findtext("a:published", "", _ATOM) or "")[:4],
            "abstract": " ".join((e.findtext("a:summary", "", _ATOM) or "").split()),
            "categories": [c.get("term") for c in e.findall("a:category", _ATOM)],
            "venue": " ".join((e.findtext("arxiv:journal_ref", "", _ATOM) or "").split()) or None,
            "doi": (e.findtext("arxiv:doi", "", _ATOM) or "").strip().lower() or None,
        })
    return out


def arxiv_meta(aid):
    aid = norm_arxiv(aid)
    if not aid:
        return None
    es = _entries(http_get(f"https://export.arxiv.org/api/query?id_list={aid}"))
    return es[0] if es else None


def arxiv_search(query, max_results=10, start=0):
    q = query.strip()
    if not re.search(r"\b(ti|abs|all|au|cat|co|jr):", q):
        terms = re.findall(r'"[^"]+"|\S+', q)
        q = " AND ".join(f"all:{t}" for t in terms)
    url = ("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": q, "start": start, "max_results": max(1, min(int(max_results), 30)),
         "sortBy": "relevance"}))
    return _entries(http_get(url))


def doi_meta(doi):
    doi = norm_doi(doi)
    if not doi:
        return None
    try:
        j = json.loads(http_get("https://api.crossref.org/works/" + urllib.parse.quote(doi)))
        m = j.get("message") or {}
        title = " ".join((m.get("title") or [""])[0].split())
        if title:
            year = ((m.get("issued") or {}).get("date-parts") or [[None]])[0][0]
            return {"doi": doi, "title": title,
                    "authors": [" ".join(filter(None, [a.get("given"), a.get("family")]))
                                for a in m.get("author") or []],
                    "year": str(year or ""), "venue": (m.get("container-title") or [None])[0],
                    "abstract": re.sub(r"<[^>]+>", " ", m.get("abstract") or "").strip() or None}
    except (NetError, json.JSONDecodeError):
        pass
    try:   # Crossref 取不到（非 Crossref 注册的 DOI）→ OpenAlex
        j = json.loads(http_get("https://api.openalex.org/works/doi:" + urllib.parse.quote(doi)))
        if j.get("title"):
            return {"doi": doi, "title": " ".join(j["title"].split()),
                    "authors": [(a.get("author") or {}).get("display_name", "")
                                for a in j.get("authorships") or []],
                    "year": str(j.get("publication_year") or ""),
                    "venue": ((j.get("primary_location") or {}).get("source") or {}).get("display_name"),
                    "abstract": None}
    except (NetError, json.JSONDecodeError):
        pass
    return None


def url_meta(url):
    if not re.match(r"^https?://", url or ""):
        return None
    raw = http_get(url)
    m = re.search(rb"<title[^>]*>(.*?)</title>", raw[:200000], re.S | re.I)
    title = " ".join(html.unescape(m.group(1).decode("utf-8", "replace")).split()) if m else ""
    return {"url": url, "title": title or url, "authors": [], "year": "", "venue": None,
            "abstract": None}


# ---------------------------------------------------------------- 全文 → 段落

VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr", "source",
        "embed", "param", "track"}


def _sec_label(sid):
    """LaTeXML 的 section id → 我们的节号：S3.SS2 → 3.2，A1.SS1 → a1.1。"""
    parts = sid.split(".")
    out = []
    for p in parts:
        m = re.match(r"^(S|A|SS|SSS)(\d+)$", p)
        if not m:
            return None
        n = int(m.group(2))
        if n == 0:
            continue
        out.append(("a" if m.group(1) == "A" else "") + str(n))
    return ".".join(out) or None


class _LatexmlParser(HTMLParser):
    """arXiv 官方 HTML（LaTeXML 产出）→ [(anchor, 节标题, 文本)]。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections = [("front", "")]     # 栈：(label, title)
        self.titles = {}
        self.items = []                     # (anchor, section_label, text)
        self.counter = {}
        self.cap = None                     # 正在收集的块：dict(tag, depth, kind, buf)
        self.skip = []                      # 跳过的子树：[tag, depth]
        self.head = None                    # 正在收集的标题
        self.stop = False
        self.tabs = self.figs = 0
        self.doc_title = None

    def _cls(self, attrs):
        return dict(attrs).get("class") or ""

    def handle_starttag(self, tag, attrs):
        if self.stop:
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in VOID:
            return
        if self.skip:
            if tag == self.skip[-1][0]:
                self.skip[-1][1] += 1
            return
        if tag == "math":
            alt = a.get("alttext")
            if alt and self.cap is not None:
                self.cap["buf"].append(f" {alt} ")
            elif alt and self.head is not None:
                self.head["buf"].append(f" {alt} ")
            self.skip.append(["math", 1])
            return
        if "ltx_note" in cls.split() or "ltx_bibliography" in cls or \
                tag in ("script", "style", "nav", "header", "footer"):
            self.skip.append([tag, 1])   # 参考文献跳过，但其后的附录照读
            return
        if tag == "section":
            sid = a.get("id") or ""
            label = _sec_label(sid)
            if label is None:       # ltx_paragraph 之类：并入上级节
                label = self.sections[-1][0]
            self.sections.append((label, ""))
            return
        if self.cap is not None:
            if tag == self.cap["tag"]:
                self.cap["depth"] += 1
            if tag in ("p", "td", "th", "li", "tr") and self.cap["buf"]:
                self.cap["buf"].append(" ")
            return
        if re.match(r"^h[1-6]$", tag) and "ltx_title" in cls:
            kind = "doc" if "ltx_title_document" in cls else (
                "abstract" if "ltx_title_abstract" in cls else "sec")
            self.head = {"tag": tag, "kind": kind, "buf": []}
            return
        if tag == "div" and "ltx_abstract" in cls:
            self.sections.append(("abstract", "Abstract"))
            self.titles["abstract"] = "Abstract"
            self._abstract_depth = 1
            return
        if getattr(self, "_abstract_depth", 0) and tag == "div":
            self._abstract_depth += 1
        if tag == "figure" and ("ltx_table" in cls or "ltx_figure" in cls):
            self.cap = {"tag": "figure", "depth": 1, "buf": [],
                        "kind": "tab" if "ltx_table" in cls else "fig"}
            return
        if (tag == "div" and "ltx_para" in cls.split()) or \
                (tag == "p" and self.sections[-1][0] == "abstract"):
            self.cap = {"tag": tag, "depth": 1, "buf": [], "kind": "para"}

    def handle_endtag(self, tag):
        if self.stop or tag in VOID:
            return
        if self.skip:
            if tag == self.skip[-1][0]:
                self.skip[-1][1] -= 1
                if self.skip[-1][1] == 0:
                    self.skip.pop()
            return
        if self.head is not None and tag == self.head["tag"]:
            text = " ".join("".join(self.head["buf"]).split())
            if self.head["kind"] == "doc":
                self.doc_title = text
            elif self.head["kind"] == "sec":
                label = self.sections[-1][0]
                self.titles.setdefault(label, text)
            self.head = None
            return
        if self.cap is not None:
            if tag == self.cap["tag"]:
                self.cap["depth"] -= 1
                if self.cap["depth"] == 0:
                    self._flush()
            return
        if tag == "section" and len(self.sections) > 1:
            self.sections.pop()
            return
        if tag == "div" and getattr(self, "_abstract_depth", 0):
            self._abstract_depth -= 1
            if self._abstract_depth == 0 and self.sections[-1][0] == "abstract":
                self.sections.pop()

    def handle_data(self, data):
        if self.stop or self.skip:
            return
        if self.head is not None:
            self.head["buf"].append(data)
        elif self.cap is not None:
            self.cap["buf"].append(data)

    def _flush(self):
        cap, self.cap = self.cap, None
        text = " ".join("".join(cap["buf"]).split())
        if not text:
            return
        label = self.sections[-1][0]
        if cap["kind"] == "tab":
            self.tabs += 1
            anchor = f"tab{self.tabs}"
        elif cap["kind"] == "fig":
            self.figs += 1
            anchor = f"fig{self.figs}"
        else:
            n = self.counter[label] = self.counter.get(label, 0) + 1
            anchor = f"{_group(label)}-p{n}"
        self.items.append((anchor, _group(label), text))


def parse_arxiv_html(raw):
    p = _LatexmlParser()
    p.feed(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    p.close()
    if sum(1 for a, _, _ in p.items if "-p" in a) < 3:
        return None             # 不像 LaTeXML 全文（可能是“HTML 不可用”页）
    return {"title": p.doc_title, "titles": {_group(k): v for k, v in p.titles.items()},
            "items": p.items}


def _group(label):
    """节号 → 锚点前缀：3.2 → s3.2；abstract、front 原样。"""
    return label if label in ("abstract", "front") else f"s{label}"


_REFS = re.compile(r"^(references|bibliography|参考文献)$", re.I)
_APPX = re.compile(r"^(appendix|appendices|supplementary material)\b|^[A-H]\s+[A-Z][a-z]", re.I)


def parse_pdf_text(text):
    """pdftotext 的输出 → [(anchor, 分组, 文本)]。

    PDF 里的节号和标题常被拆成两个块、又和页码、图中文字混在一起（spike 实测 OpenVLA 的 PDF），
    按节编号不可靠；页是确定的。所以 PDF 一律按「页-段」编锚点：pg3-p4 = 第 3 页第 4 段。
    识别出的“Abstract”段仍编为 abstract-p1。参考文献跳过，其后的附录照读。
    """
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)      # 行末连字符断词
    items, counter = [], {}
    in_refs, seen_abs = False, False
    for pno, page in enumerate(text.split("\f"), 1):
        for b in re.split(r"\n\s*\n", page):
            one = " ".join(b.split())
            if not one:
                continue
            if _REFS.match(one):
                in_refs = True
                continue
            if in_refs:
                if _APPX.match(one) and len(one) < 80:
                    in_refs = False
                continue
            if not seen_abs and re.match(r"^abstract\b[\s.:—-]*", one, re.I):
                seen_abs = True
                rest = re.sub(r"^abstract\b[\s.:—-]*", "", one, flags=re.I)
                if len(rest) >= 40:
                    items.append(_pdf_item("abstract", rest, counter))
                continue
            if len(one) < 40:       # 页码、页眉、节号、图中文字碎片
                continue
            items.append(_pdf_item(f"pg{pno}", one, counter))
    if len(items) < 3:
        return None
    titles = {g: (f"第 {g[2:]} 页" if g.startswith("pg") else "Abstract") for _, g, _ in items}
    return {"title": None, "titles": titles, "items": items}


def _pdf_item(group, text, counter):
    n = counter[group] = counter.get(group, 0) + 1
    return (f"{group}-p{n}", group, text)


def pdf_to_text(path):
    try:
        p = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True,
                           timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise NetError(f"pdftotext 不可用或超时：{e}") from e
    if p.returncode != 0:
        raise NetError(f"pdftotext 失败：{p.stderr.decode('utf-8', 'replace')[:300]}")
    return p.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------- 库

class Library:
    def __init__(self, root):
        self.root = Path(root)

    def dir(self, pid):
        return self.root / pid

    def fulltext_path(self, pid):
        return self.dir(pid) / "fulltext.md"

    def anchors(self, pid):
        p = self.dir(pid) / "anchors.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def meta(self, pid):
        p = self.dir(pid) / "meta.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def has_fulltext(self, pid):
        return any(not a.startswith("abstract-") for a in self.anchors(pid))

    def write(self, pid, title, parsed, source, abstract=None, raw=None, raw_name=None):
        d = self.dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            (d / raw_name).write_bytes(raw)
        items = list(parsed["items"]) if parsed else []
        if abstract and not any(g == "abstract" for _, g, _ in items):
            items.insert(0, ("abstract-p1", "abstract", " ".join(abstract.split())))
        titles = dict(parsed["titles"]) if parsed else {}
        titles.setdefault("abstract", "Abstract")
        lines = [f"# {title}", "",
                 f"> {pid} · 来源：{source} · 每段开头的方括号是锚点，引用证据时写锚点"
                 "（s3.2-p4 = 第 3.2 节第 4 段；pg3-p4 = PDF 第 3 页第 4 段；tab1 = 第 1 个表；"
                 "abstract-p1 = 摘要）。整节可以写节锚点，如 s3.2、pg3。", ""]
        cur = None
        for anchor, group, text in items:
            if group != cur and not anchor.startswith(("tab", "fig")):
                cur = group
                lines += [f"## [{group}] {titles.get(group, '')}".rstrip(), ""]
            lines += [f"[{anchor}] {text}", ""]
        (d / "fulltext.md").write_text("\n".join(lines), encoding="utf-8")
        anchors = {a: t for a, _, t in items}
        (d / "anchors.json").write_text(json.dumps(anchors, ensure_ascii=False), encoding="utf-8")
        sha = hashlib.sha256(json.dumps(anchors, ensure_ascii=False).encode()).hexdigest()[:16]
        m = {"source": source, "sha": sha, "anchors": len(anchors),
             "sections": sorted({l for _, l, _ in items}), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if raw is not None:
            m["raw"] = raw_name
            m["raw_sha256"] = hashlib.sha256(raw).hexdigest()
        (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        return m

    def fetch_arxiv(self, pid, aid, title, abstract):
        """arXiv HTML 优先，失败退 PDF。返回 (是否拿到全文, 说明)。"""
        try:
            raw = http_get(f"https://arxiv.org/html/{aid}")
            parsed = parse_arxiv_html(raw)
            if parsed:
                m = self.write(pid, title, parsed, f"arXiv HTML {aid}", abstract, raw, "source.html")
                return True, f"arXiv HTML，{m['anchors']} 个锚点"
        except NetError:
            pass
        try:
            raw = http_get(f"https://arxiv.org/pdf/{aid}", timeout=180)
            if raw[:4] == b"%PDF":
                d = self.dir(pid)
                d.mkdir(parents=True, exist_ok=True)
                (d / "source.pdf").write_bytes(raw)
                parsed = parse_pdf_text(pdf_to_text(d / "source.pdf"))
                if parsed:
                    m = self.write(pid, title, parsed, f"arXiv PDF {aid}（pdftotext）", abstract,
                                   raw, "source.pdf")
                    return True, f"arXiv PDF，{m['anchors']} 个锚点"
        except NetError as e:
            self.write(pid, title, None, "仅摘要", abstract)
            return False, f"全文获取失败（{e}），只有摘要"
        self.write(pid, title, None, "仅摘要", abstract)
        return False, "全文无法解析，只有摘要"

    def ingest_pdf(self, pid, title, raw, abstract=None):
        """人上传的 PDF（M4.5）。"""
        if raw[:4] != b"%PDF":
            raise ValueError("不是 PDF 文件")
        d = self.dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "source.pdf").write_bytes(raw)
        parsed = parse_pdf_text(pdf_to_text(d / "source.pdf"))
        if not parsed:
            raise ValueError("PDF 转文本后内容过少（可能是扫描件），无法建立段落锚点")
        return self.write(pid, title, parsed, "人上传的 PDF（pdftotext）", abstract, raw, "source.pdf")

    # ------------------------------------------------------------ 引文校验

    def resolve(self, pid, locator):
        """locator（锚点列表）→ {anchor: text}。节锚点（s3、s3.2、abstract）展开为其下所有段落。"""
        anchors = self.anchors(pid)
        out, missing = {}, []
        for loc in locator:
            loc = loc.strip().strip("[]").lower()
            if loc in anchors:
                out[loc] = anchors[loc]
                continue
            hits = {a: t for a, t in anchors.items()
                    if re.match(rf"^{re.escape(loc)}(\.[\da-z.]+)?-p\d+$", a)}
            if hits:
                out.update(hits)
            else:
                missing.append(loc)
        return out, missing

    def verify_quote(self, pid, locator, quote):
        """返回 (ok, 说明, basis)。basis = abstract / fulltext。"""
        if not locator:
            return False, "locator 不能为空：写出引文所在的段落锚点（见 fulltext.md 每段开头的方括号）", None
        found, missing = self.resolve(pid, locator)
        if missing:
            return False, (f"锚点 {missing} 在 {pid} 的全文里不存在。锚点以 fulltext.md 每段开头的方括号为准"
                           "（如 s3.2-p4、tab1、abstract-p1）"), None
        q = _norm(quote)
        if len(q) < MIN_QUOTE:
            return False, "quote 太短：摘录能单独说明问题的一句原文（英文原文，不要翻译）", None
        if len(quote) > MAX_QUOTE * 2:
            return False, f"quote 太长：摘录关键的一两句（≤{MAX_QUOTE} 字符左右）", None
        if not any(q in _norm(t) for t in found.values()) and q not in _norm(" ".join(found.values())):
            return False, ("quote 与所引段落对不上：必须是这些段落里的原文摘录（逐字，可省略首尾），"
                           "不能是转述或翻译。先重新读一遍该段落。"), None
        basis = "abstract" if all(a.startswith("abstract-") for a in found) else "fulltext"
        return True, "ok", basis


def _norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    return "".join(ch for ch in s if ch.isalnum())
