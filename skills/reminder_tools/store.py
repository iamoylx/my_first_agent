# -*- coding: utf-8 -*-
"""提醒任务存储（TaskStore）。

与记忆数据完全分离：任务写入独立 task_data/ 目录，绝不触碰 memory_data/。
- 原子写（tmp + os.replace），并发安全（线程锁）。
- 支持一次性 / 每日 / 每周重复任务。
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta


def parse_when(s, now=None):
    """解析提醒时间：支持 'YYYY-MM-DD HH:MM' / 'YYYY/MM/DD HH:MM' / '今天|明天|后天 HH:MM'。"""
    now = now or datetime.now()
    if not s:
        return None
    s = str(s).strip()
    m = re.match(r"^(今天|明天|后天)\s*(\d{1,2})[:：](\d{2})$", s)
    if m:
        day = now.date() + timedelta(days={"今天": 0, "明天": 1, "后天": 2}[m.group(1)])
        return datetime(day.year, day.month, day.day, int(m.group(2)), int(m.group(3)))
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class TaskStore:
    def __init__(self, base_dir, user_id="default"):
        self._path = os.path.join(str(base_dir), user_id, "tasks.json")
        self._lock = threading.Lock()
        self._tasks = None
        self._mtime = None

    # ---------- 基础读写 ----------
    def _load(self):
        """读任务列表；用文件 mtime 检测外部写入（多实例/多进程可见最新任务）。"""
        try:
            mtime = os.path.getmtime(self._path)
        except OSError:
            mtime = None
        if self._tasks is None or mtime != self._mtime:
            self._mtime = mtime
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._tasks = json.load(f)
            except FileNotFoundError:
                self._tasks = []
            except Exception:
                self._tasks = []
        return self._tasks

    def _save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._tasks, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    # ---------- 对外接口 ----------
    def add(self, reminder, when, repeat="none", now=None):
        """创建提醒。when 见 parse_when。返回 (task_id, None) 或 (None, 错误信息)。"""
        now = now or datetime.now()
        dt = parse_when(when, now)
        if dt is None:
            return None, f"无法解析提醒时间：{when}（支持 YYYY-MM-DD HH:MM 或 今天/明天 HH:MM）"
        if dt <= now:
            return None, f"提醒时间已过：{when}（当前 {now.strftime('%Y-%m-%d %H:%M')}）"
        repeat = repeat if repeat in ("daily", "weekly") else "none"
        with self._lock:
            tasks = self._load()
            tid = f"r{int(time.time() * 1000)}"
            tasks.append({
                "id": tid,
                "reminder": str(reminder),
                "when": dt.strftime("%Y-%m-%d %H:%M"),
                "repeat": repeat,
                "created_at": now.isoformat(),
                "done": False,
                "last_fired": None,
            })
            self._save()
        return tid, None

    def list(self):
        """未完成任务（含未来与已到期未触发）。"""
        with self._lock:
            return [t for t in self._load() if not t.get("done")]

    def delete(self, tid):
        with self._lock:
            tasks = self._load()
            before = len(tasks)
            tasks[:] = [t for t in tasks if str(t.get("id")) != str(tid)]
            if len(tasks) != before:
                self._save()
                return True
        return False

    def due(self, now=None):
        """返回到点未触发的任务；重复任务自动推进到下次，一次性任务标记完成。"""
        now = now or datetime.now()
        fired = []
        with self._lock:
            tasks = self._load()
            changed = False
            for t in tasks:
                if t.get("done"):
                    continue
                dt = parse_when(t.get("when"))
                if dt is None:
                    continue
                if dt <= now:
                    fired.append(t)
                    t["last_fired"] = now.isoformat()
                    if t.get("repeat") == "daily":
                        t["when"] = (dt + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
                    elif t.get("repeat") == "weekly":
                        t["when"] = (dt + timedelta(weeks=1)).strftime("%Y-%m-%d %H:%M")
                    else:
                        t["done"] = True
                    changed = True
            if changed:
                self._save()
        return fired

    def path(self):
        return self._path
