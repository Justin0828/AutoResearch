"""命令行入口：python -m autoresearch <命令>（或仓库根下的 ./ar）。"""
import argparse
import signal
import sys
import threading

from . import bootstrap, briefing, config, schema
from .store import Store


def cmd_init(cfg, a):
    st, created = bootstrap.init(cfg, seed=not a.empty)
    print(f"State 仓库{'已创建' if created else '已存在'}：{cfg.state}")


def cmd_validate(cfg, a):
    errs, warns = schema.validate_repo(cfg.state)
    for e in errs:
        print("ERROR ", e)
    for w in warns:
        print("WARN  ", w)
    print(f"{len(errs)} 个错误，{len(warns)} 个警告")
    return 1 if errs else 0


def cmd_brief(cfg, a):
    """打印一份 briefing——用来肉眼检查“新 session 能看到什么”。"""
    st = Store(cfg.state, cfg.lock)
    task = {"id": "T-preview", "kind": a.profile, "goal": "（预览）", "discussion": a.discussion}
    print(briefing.Assembler(st).briefing(a.profile, task))


def cmd_serve(cfg, a):
    from .daemon import AlreadyRunning, Daemon, instance_lock
    from .events import Bus
    from .server import App

    try:
        lock = instance_lock(cfg)      # noqa: F841  持有到进程结束
    except AlreadyRunning as e:
        sys.exit(f"没有启动：{e}")
    st, _ = bootstrap.init(cfg)
    bus = Bus(cfg)
    daemon = Daemon(cfg, st, bus)
    daemon.start()
    httpd = App(cfg, st, bus, daemon).serve()

    def shutdown(*_):
        print("\n正在停止：切断在飞任务并写交接记录…", flush=True)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"AutoResearch 已启动：http://{cfg.host}:{cfg.port}  （State: {cfg.state}）", flush=True)
    print(f"远程访问：ssh -L {cfg.port}:localhost:{cfg.port} <本机>", flush=True)
    try:
        httpd.serve_forever()
    finally:
        daemon.stop()
        print("已停止。", flush=True)


def cmd_repair_papers(cfg, a):
    from . import papers
    from .library import Library
    st = Store(cfg.state, cfg.lock)
    for pid, msg in papers.repair_arxiv_dois(st, Library(cfg.library)):
        print(pid, msg)


def cmd_status(cfg, a):
    for name in ("daemon.json", "quota.json"):
        p = cfg.run / name
        print(f"== {name}")
        print(p.read_text(encoding="utf-8") if p.exists() else "（无）")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ar", description="AutoResearch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="创建 State 仓库")
    p.add_argument("--empty", action="store_true", help="不从 Phase 0 fixture 播种")
    sub.add_parser("validate", help="校验 State 仓库")
    p = sub.add_parser("brief", help="打印一份 briefing")
    p.add_argument("profile", choices=sorted(briefing.PROFILES))
    p.add_argument("--discussion", default=None)
    sub.add_parser("serve", help="启动 daemon 与前端")
    sub.add_parser("status", help="查看班次与额度状态")
    sub.add_parser("repair-papers", help="补取按 arXiv DOI 登记、却没取全文的论文")
    a = ap.parse_args(argv)
    cfg = config.load()
    fn = {"init": cmd_init, "validate": cmd_validate, "brief": cmd_brief,
          "serve": cmd_serve, "status": cmd_status,
          "repair-papers": cmd_repair_papers}[a.cmd]
    return fn(cfg, a) or 0


if __name__ == "__main__":
    sys.exit(main())
