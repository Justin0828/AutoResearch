"""事件总线（M8.1 的最小形态）：内存扇出给 SSE 订阅者，同时落盘供回看。"""
import itertools
import json
import queue
import threading

from .store import now


class Bus:
    def __init__(self, cfg):
        self.path = cfg.run / "events.jsonl"
        self.notes_path = cfg.run / "notifications.jsonl"
        self._subs = set()
        self._lock = threading.Lock()
        self._seq = itertools.count(1)

    def publish(self, type_, data=None, persist=True):
        ev = {"seq": next(self._seq), "ts": now(), "type": type_, "data": data or {}}
        if persist:
            with self._lock, open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass
        return ev

    def notify(self, level, text, **extra):
        """通知中心（M9.12）：额度暂停、交接、需要人处理的事。"""
        note = {"ts": now(), "level": level, "text": text, **extra}
        with self._lock, open(self.notes_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(note, ensure_ascii=False) + "\n")
        self.publish("notification", note, persist=False)

    def notifications(self, limit=50):
        if not self.notes_path.exists():
            return []
        lines = self.notes_path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(l) for l in reversed(lines) if l.strip()]

    def subscribe(self):
        q = queue.Queue(maxsize=2000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q):
        self._subs.discard(q)
