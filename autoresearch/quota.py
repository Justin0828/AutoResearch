"""额度状态（M2.1）：直接读 stream-json 的 rate_limit_event，不做任何估算。

事件形态（Phase 0 实测）：
  {"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":...,
   "rateLimitType":"five_hour","unifiedWindows":{"five_hour":{"utilization":0.06,
   "resetsAt":1790160600},"seven_day":{"utilization":0.19,"resetsAt":1790391600}}}}
"""
import json
import threading
import time

OK_STATUSES = {"allowed", "allowed_warning"}


class Quota:
    def __init__(self, cfg):
        self.path = cfg.run / "quota.json"
        self.weekly_stop = cfg.weekly_stop
        self._lock = threading.Lock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def update(self, info):
        """吃一条 rate_limit_info。返回是否已耗尽（'quota_5h' / 'quota_7d' / None）。"""
        win = info.get("unifiedWindows") or {}
        with self._lock:
            d = self.data
            for name in ("five_hour", "seven_day"):
                w = win.get(name) or {}
                if "utilization" in w:
                    d[name] = float(w["utilization"])
                if w.get("resetsAt"):
                    d[f"{name}_resets"] = int(w["resetsAt"])
            d["status"] = info.get("status")
            d["type"] = info.get("rateLimitType")
            if info.get("resetsAt"):
                d["resets"] = int(info["resetsAt"])
            d["seen"] = int(time.time())
            self._save()
        return self.exhausted_by(info)

    def exhausted_by(self, info):
        if info.get("status") and info["status"] not in OK_STATUSES:
            return "quota_7d" if info.get("rateLimitType") == "seven_day" else "quota_5h"
        if (self.data.get("seven_day") or 0) >= self.weekly_stop:
            return "quota_7d"
        return None

    def mark_exhausted(self, reason, resets=None):
        with self._lock:
            self.data["status"] = "rejected"
            self.data["type"] = "seven_day" if reason == "quota_7d" else "five_hour"
            if resets:
                self.data["resets"] = int(resets)
            self._save()

    def gate(self, now=None):
        """任务启动前检查（M2.1：检查时机在任务启动前）。

        返回 (None, None) 表示放行；否则 (reason, resume_at_epoch)。
        """
        now = now or time.time()
        d = self.data
        if (d.get("seven_day") or 0) >= self.weekly_stop:
            r = d.get("seven_day_resets")
            if not r or r > now:
                return "quota_7d", r
        if d.get("status") and d["status"] not in OK_STATUSES:
            r = d.get("resets") or d.get("five_hour_resets")
            if r and r > now:
                return ("quota_7d" if d.get("type") == "seven_day" else "quota_5h"), r
        return None, None

    def snapshot(self):
        d = dict(self.data)
        for k in ("five_hour_resets", "seven_day_resets", "resets"):
            if d.get(k):
                d[k + "_iso"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(d[k]))
        d["weekly_stop"] = self.weekly_stop
        return d

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        tmp.replace(self.path)
