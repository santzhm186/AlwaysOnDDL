# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Small, bounded relevance memory scoped to an explicit work cycle.

Keys hash the goal, exact normalized window, policy, service and prompt version.
No global process allowlist is inferred from one window's relevance.
"""
from collections import OrderedDict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from .activity_log import redact


CACHE_MODES = {"本工作周期": None, "15分钟": 900, "30分钟": 1800, "60分钟": 3600}


def review_identity(goal, process, title, policy, endpoint, model, revision, scope):
    normalized = (process.strip().casefold(), " ".join(title.split()).casefold())
    data = [revision, scope, goal, normalized, policy.to_dict(), endpoint, model]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


class ReviewMemory:
    def __init__(self, data_dir: Path | None, *, limit=512):
        self.path = Path(data_dir) / "ai_judgments.json" if data_dir is not None else None
        self.limit = limit
        self.entries = OrderedDict()
        self.warning = ""
        if self.path and self.path.exists():
            try:
                if self.path.stat().st_size > 1024 * 1024:
                    raise ValueError("oversized")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("schema_version") != 1 or not isinstance(data.get("entries"), list):
                    raise ValueError("invalid cache")
                for item in data["entries"][-self.limit:]:
                    key, value = item["identity"], item["value"]
                    if (not isinstance(key, str) or len(key) != 64 or any(char not in "0123456789abcdef" for char in key)
                            or not isinstance(value, dict) or set(value) != {"scope", "created_at", "verdict", "reason"}
                            or not isinstance(value["scope"], str) or len(value["scope"]) > 64
                            or type(value["created_at"]) not in (float, int) or not math.isfinite(value["created_at"])
                            or value["verdict"] not in {"related", "neutral", "unrelated", "unknown"}
                            or not isinstance(value["reason"], str) or len(value["reason"]) > 500):
                        raise ValueError("invalid cache entry")
                    self.entries[key] = redact(value)
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self.entries.clear()
                self.warning = "旧判断缓存无法读取，本次重新审核；日志和配置保持原样。"

    def lookup(self, identity, scope, ttl=None):
        value = self.entries.get(identity)
        if not value or value["scope"] != scope:
            return None
        age = time.time() - value["created_at"]
        effective_ttl = min(ttl or 300, 300) if value["verdict"] == "unknown" else ttl
        if age < 0 or (effective_ttl is not None and age >= effective_ttl):
            return None
        self.entries.move_to_end(identity)
        return {"verdict": value["verdict"], "reason": value["reason"], "available": True,
                "cached": True, "error_code": ""}

    def remember(self, identity, scope, result):
        if (not result.get("available") or result.get("verdict") not in {"related", "neutral", "unrelated", "unknown"}
                or not isinstance(result.get("reason"), str)):
            return
        self.entries[identity] = {"scope": scope, "created_at": time.time(),
                                  "verdict": result["verdict"], "reason": redact(result["reason"])[:500]}
        self.entries.move_to_end(identity)
        while len(self.entries) > self.limit:
            self.entries.popitem(last=False)
        self._save()

    def _save(self):
        if not self.path:
            return
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix="ai_judgments.", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"schema_version": 1, "entries": [{"identity": key, "value": value}
                          for key, value in self.entries.items()]}, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.warning = ""
        except OSError:
            self.warning = "判断缓存未能保存，本次运行仍可复用；重开后可能需要重新审核。"
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def clear(self):
        self.entries.clear()
        self.warning = ""
        if self.path:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                self.warning = "磁盘缓存未能清理；本次运行已清空，请检查目录权限。"
