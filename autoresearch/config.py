"""路径与阈值。全部可由环境变量覆盖，不硬编码路径（M10 可迁移性）。"""
import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent


def _env_path(name, default):
    return Path(os.environ.get(name) or default).expanduser().resolve()


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class Config:
    def __init__(self):
        # 不放在代码仓库下：agent 的 cwd 若位于代码仓库之内会加载开发用 CLAUDE.md
        self.root = _env_path("AR_ROOT", "~/autoresearch")
        self.state = self.root / "state"
        self.run = self.root / "run"
        self.tasks = self.run / "tasks"
        self.lock = self.run / "state.lock"
        self.claude_bin = os.environ.get("AR_CLAUDE_BIN", "claude")
        self.model = os.environ.get("AR_MODEL") or None
        self.host = "127.0.0.1"                     # M9：只绑本机
        self.port = int(os.environ.get("AR_PORT", "8765"))
        self.weekly_stop = _env_float("AR_WEEKLY_STOP", 0.95)   # M2.1 周额度硬闸
        self.auto_resume = os.environ.get("AR_AUTO_RESUME", "1") != "0"
        self.distill_every = int(os.environ.get("AR_DISTILL_EVERY", "3"))
        self.sweep_seconds = int(os.environ.get("AR_SWEEP_SECONDS", "30"))

    def ensure(self):
        for d in (self.root, self.run, self.tasks):
            d.mkdir(parents=True, exist_ok=True)
        return self


def load():
    return Config().ensure()
