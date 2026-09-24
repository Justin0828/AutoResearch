"""Research State 仓库的读写与提交（DESIGN.md §5.1 / §5.4）。

所有写入都在 `Store.tx()` 里完成：持锁 → 写文件 → 只提交本次写入的路径。
锁是 `run/state.lock` 上的 flock，对后端线程与 MCP server 子进程同样有效。
"""
import contextlib
import datetime
import fcntl
import json
import os
import re
import subprocess
from pathlib import Path

from . import frontmatter, schema

ACTORS = {
    "agent": ("ar-agent", "agent@autoresearch.local"),
    "human": ("ar-human", "human@autoresearch.local"),
    "system": ("ar-system", "system@autoresearch.local"),
}


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, state, lock_path):
        self.state = Path(state)
        self.lock_path = Path(lock_path)

    # ------------------------------------------------------------ git / lock

    @contextlib.contextmanager
    def locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def git(self, *args, actor="system", check=True):
        name, email = ACTORS[actor]
        env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
                   GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
        p = subprocess.run(["git", "-c", "commit.gpgsign=false", *args],
                           cwd=self.state, env=env, capture_output=True, text=True)
        if check and p.returncode != 0:
            raise StoreError(f"git {' '.join(args)} 失败：{p.stderr.strip() or p.stdout.strip()}")
        return p.stdout

    def _commit(self, paths, message, actor, task=None):
        paths = sorted({str(p) for p in paths})
        if not paths:
            return None
        self.git("add", "-A", "--", *paths, actor=actor)
        staged = self.git("diff", "--cached", "--name-only", "--", *paths, actor=actor).strip()
        if not staged:
            return None
        msg = message + (f"\n\nAR-Task: {task}" if task else "")
        self.git("commit", "-q", "-m", msg, "--", *paths, actor=actor)
        return self.git("rev-parse", "--short", "HEAD").strip()

    @contextlib.contextmanager
    def tx(self, message, actor="system", task=None):
        """一次原子写入：持锁，yield 一个 Tx，退出时只提交 Tx 写过的路径。"""
        with self.locked():
            t = Tx(self)
            yield t
            t.commit_sha = self._commit(t.paths, message if not t.note else f"{message}：{t.note}",
                                        actor, task)

    def dirty_paths(self):
        out = self.git("status", "--porcelain", "-z", "--untracked-files=all")
        paths = []
        for entry in out.split("\0"):
            if len(entry) > 3:
                paths.append(entry[3:])
        return paths

    def sweep(self, message, actor):
        """把工作区里所有未提交的改动提交掉（人手改 / 被 SIGKILL 遗留）。"""
        with self.locked():
            paths = self.dirty_paths()
            return (self._commit(paths, message, actor), paths) if paths else (None, [])

    def head(self):
        return self.git("rev-parse", "HEAD", check=False).strip() or None

    def log(self, since=None, limit=200):
        rng = [f"{since}..HEAD"] if since else []
        out = self.git("log", *rng, f"-n{limit}", "--name-only",
                       "--format=%x1e%h%x1f%an%x1f%aI%x1f%s", check=False)
        commits = []
        for rec in out.split("\x1e"):
            if not rec.strip():
                continue
            head, *files = rec.strip("\n").split("\n")
            sha, author, date, subj = head.split("\x1f")
            commits.append({"sha": sha, "author": author, "date": date, "subject": subj,
                            "files": [f for f in files if f.strip()]})
        return commits

    # ------------------------------------------------------------ objects

    def read(self, rel):
        p = self.state / rel
        return p.read_text(encoding="utf-8") if p.exists() else None

    def read_obj(self, ident):
        p = schema.path_of(self.state, ident)
        if p is None or not p.exists():
            return None, None
        return frontmatter.parse(p.read_text(encoding="utf-8"))

    def exists(self, ident):
        if ident and ident.startswith("DS"):
            return (self.state / "discussions" / ident).is_dir()
        p = schema.path_of(self.state, ident)
        return bool(p and p.exists())

    def list(self, type_):
        k = schema.KINDS[type_]
        d = self.state / k.dir
        out = []
        for p in sorted(d.glob(f"{k.prefix}*.md")) if d.is_dir() else []:
            if schema.split_id(p.stem)[0] != k.prefix:
                continue
            try:
                meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
            except frontmatter.FrontmatterError:
                meta, body = None, ""
            out.append((meta or {"id": p.stem}, body))
        return out

    def next_id(self, prefix, sub):
        """必须在锁内调用。被撤回确认删掉的 id 不复用（§5.15.6）。"""
        d = self.state / sub
        d.mkdir(parents=True, exist_ok=True)
        n = [int(m.group(1)) for f in os.listdir(d)
             if (m := re.match(rf"^{prefix}(\d+)(?:\.md)?$", f))]
        n += [int(i[len(prefix):]) for i in self.undone_ids() if schema.split_id(i)[0] == prefix]
        return f"{prefix}{(max(n) + 1) if n else 1:03d}"

    def undone_ids(self):
        """撤回确认时删掉的对象 id：记在 Decision 的 undid 字段里。"""
        d = self.state / "decisions"
        out = []
        for p in d.glob("DEC*.md") if d.is_dir() else []:
            m = re.search(r"^undid: (\S+)$", p.read_text(encoding="utf-8").split("\n---", 1)[0], re.M)
            if m:
                out.append(m.group(1))
        return out

    def provenance(self):
        t = self.read("provenance.json")
        return json.loads(t) if t else {}

    def project(self):
        meta, body = frontmatter.parse(self.read("project.md") or "")
        return meta or {}, body


class Tx:
    def __init__(self, store):
        self.store = store
        self.paths = set()
        self.note = ""
        self.commit_sha = None

    def write(self, rel, text):
        p = self.store.state / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self.paths.add(str(rel))

    def append(self, rel, text):
        p = self.store.state / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(text)
        self.paths.add(str(rel))

    def write_obj(self, ident, meta, body):
        k = schema.kind_of_id(ident)
        self.write(f"{k.dir}/{ident}.md", frontmatter.dump(meta, body))

    def new_id(self, type_):
        k = schema.KINDS[type_]
        return self.store.next_id(k.prefix, k.dir)

    def remove(self, rel):
        p = self.store.state / rel
        if p.exists():
            p.unlink()
        self.paths.add(str(rel))

    def del_provenance(self, ident):
        prov = self.store.provenance()
        prov.pop(ident, None)
        self.write("provenance.json",
                   json.dumps(dict(sorted(prov.items())), ensure_ascii=False, indent=1) + "\n")

    def set_provenance(self, ident, record):
        prov = self.store.provenance()
        prov[ident] = record
        self.write("provenance.json",
                   json.dumps(dict(sorted(prov.items())), ensure_ascii=False, indent=1) + "\n")
