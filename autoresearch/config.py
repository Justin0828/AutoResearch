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
        self.library = self.root / "library"      # 论文原件与全文，不进 State 的 git（§5.5）
        self.claude_bin = os.environ.get("AR_CLAUDE_BIN", "claude")
        self.model = os.environ.get("AR_MODEL") or None
        self.host = "127.0.0.1"                     # M9：只绑本机
        self.port = int(os.environ.get("AR_PORT", "8765"))
        self.weekly_stop = _env_float("AR_WEEKLY_STOP", 0.95)   # M2.1 周额度硬闸
        self.auto_resume = os.environ.get("AR_AUTO_RESUME", "1") != "0"
        self.distill_every = int(os.environ.get("AR_DISTILL_EVERY", "3"))
        self.sweep_seconds = int(os.environ.get("AR_SWEEP_SECONDS", "30"))
        # 无人值守（验证模式、夜间预习）的额度护栏（§5.7，2026-09-23 用户确认默认值）
        self.unattended_cap = _env_float("AR_UNATTENDED_CAP", 0.60)   # 5h 窗口利用率上限
        self.searches_per_target = int(os.environ.get("AR_SEARCHES_PER_TARGET", "2"))
        self.reads_per_target = int(os.environ.get("AR_READS_PER_TARGET", "6"))
        self.prep_idle_minutes = int(os.environ.get("AR_PREP_IDLE_MIN", "90"))
        # 夜间预习（M2.0）：0 关掉——不再在你离开后自动读论文；空闲自动演进不受影响（它有自己的 AR_EVOLVE_AUTO）
        self.prep_auto = os.environ.get("AR_PREP_AUTO", "1") != "0"
        self.prep_max_tasks = int(os.environ.get("AR_PREP_MAX_TASKS", "6"))
        # 自演进（§5.18）：研究者离开后的空闲窗口自动演进一篇；0 关掉（手动仍可用）
        self.evolve_auto = os.environ.get("AR_EVOLVE_AUTO", "1") != "0"

    def ensure(self):
        for d in (self.root, self.run, self.tasks, self.library):
            d.mkdir(parents=True, exist_ok=True)
        return self


def load():
    return Config().ensure()
