# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""User-owned checklist data and deterministic AI reference selection."""
import re
import uuid

MAX_TODOS = 100
MAX_TODO_TEXT = 500


def validate_todos(value):
    if not isinstance(value, list) or len(value) > MAX_TODOS:
        raise ValueError(f"待办列表最多 {MAX_TODOS} 项。")
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "text", "done"}:
            raise ValueError("待办格式无效。")
        identifier, text, done = item["id"], item["text"], item["done"]
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier)
                or identifier in seen or not isinstance(text, str) or not text.strip()
                or len(text) > MAX_TODO_TEXT or type(done) is not bool):
            raise ValueError("待办编号、内容或完成状态无效。")
        seen.add(identifier)
        result.append({"id": identifier, "text": text, "done": done})
    return result


def new_todo(text):
    if not isinstance(text, str):
        raise ValueError("待办内容必须是文本。")
    text = text.strip()
    if not text or len(text) > MAX_TODO_TEXT:
        raise ValueError(f"每项待办需要 1 至 {MAX_TODO_TEXT} 个字符。")
    return {"id": uuid.uuid4().hex, "text": text, "done": False}


def reference_goal(long_term, todos, limit=4000):
    """Pending tasks exclusively; the long-term goal is used only with none pending.

    Distribute the character budget across all tasks, instead of silently losing
    later items when the HTTP client truncates its input. Full text stays local.
    """
    pending = [" ".join(item["text"].split()) for item in todos if not item["done"]]
    if not pending:
        return long_term.strip()[:limit]
    heading = "当前短期目标（仅未完成待办）：\n"
    prefixes = [f"{index + 1}. " for index in range(len(pending))]
    budget = limit - len(heading) - sum(map(len, prefixes)) - (len(pending) - 1)
    share, extra = divmod(max(0, budget), len(pending))
    lines = []
    for index, (prefix, text) in enumerate(zip(prefixes, pending)):
        size = share + (index < extra)
        clipped = text if len(text) <= size else text[:max(0, size - 1)] + ("…" if size else "")
        lines.append(prefix + clipped)
    return (heading + "\n".join(lines))[:limit]
