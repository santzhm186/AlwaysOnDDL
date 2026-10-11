# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Side-effect-free execution policy, with explicit JSON persistence helpers.

The engine never reads windows, invokes a model, locks a device, or deletes files.
Only caller-supplied observations and elapsed active time can advance it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
import math
import os
from pathlib import Path
import tempfile
import uuid
from typing import Any


@dataclass
class Policy:
    privacy_keywords: list[str] = field(default_factory=lambda: ["密码", "password", "1password", "keepass"])
    blocked_processes: list[str] = field(default_factory=list)
    blocked_titles: list[str] = field(default_factory=list)
    allowed_processes: list[str] = field(default_factory=list)
    allowed_titles: list[str] = field(default_factory=list)
    reminder_after_seconds: float = 30.0
    escalation_after_seconds: float = 900.0
    recovery_seconds: float = 60.0
    quiet_seconds: float = 900.0
    idle_threshold_seconds: float = 120.0
    max_sample_gap_seconds: float = 30.0
    lock_enabled: bool = False
    max_stage: int = 3

    def __post_init__(self) -> None:
        for name in ("privacy_keywords", "blocked_processes", "blocked_titles", "allowed_processes", "allowed_titles"):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or any(not isinstance(item, str) for item in values):
                raise ValueError(f"{name} must be a list of strings")
            setattr(self, name, list(dict.fromkeys(item.strip().casefold() for item in values if item.strip())))
        for name in ("reminder_after_seconds", "escalation_after_seconds", "recovery_seconds", "quiet_seconds", "idle_threshold_seconds", "max_sample_gap_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
            setattr(self, name, float(value))
        if type(self.lock_enabled) is not bool:
            raise ValueError("lock_enabled must be boolean")
        if type(self.max_stage) is not int or self.max_stage != 3:
            raise ValueError("This policy version requires exactly three stages")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Policy":
        if not isinstance(data, dict):
            raise ValueError("Policy must be an object")
        allowed = {item.name for item in fields(cls)}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("Unknown policy fields: " + ", ".join(sorted(unknown)))
        return cls(**data)


@dataclass(frozen=True)
class Observation:
    process: str = ""
    title: str = ""
    status: str = "ok"
    idle_seconds: float | None = 0.0
    locked: bool = False
    semantic: str = "unknown"


def classify(process: str, title: str, policy: Policy) -> str:
    process, title = (process or "").casefold(), (title or "").casefold()
    if any(word in process or word in title for word in policy.privacy_keywords):
        return "PRIVATE"
    if not process and not title:
        return "UNKNOWN"
    if any(word in process for word in policy.blocked_processes):
        return "DRIFT"
    if any(word in process for word in policy.allowed_processes):
        return "ON_TASK"
    blocked = any(word in title for word in policy.blocked_titles)
    allowed = any(word in title for word in policy.allowed_titles)
    if blocked and allowed:
        return "GRAY"
    if blocked:
        return "DRIFT"
    if allowed:
        return "ON_TASK"
    return "GRAY"


def _fresh_state() -> dict[str, Any]:
    return {"schema_version": 1, "status": "idle", "id": "", "goal": "", "oath": "",
            "reality": "", "consequence": "",
            "stage": 0, "drift_seconds": 0.0, "recovery_seconds": 0.0,
            "quiet_remaining": 0.0, "next_reminder_at": 30.0,
            "gray_hint_remaining": 0.0, "return_pending": False,
            "last_classification": "UNKNOWN", "last_choice": "", "lock_requests": 0}


class Engine:
    """An explicit, device-independent execution cycle. `state` is for display only."""

    def __init__(self, policy: Policy | None = None, snapshot: dict[str, Any] | None = None):
        self.policy = policy or Policy()
        self.state = _fresh_state()
        self.state["next_reminder_at"] = self.policy.reminder_after_seconds
        self._discard_next_tick = True
        if snapshot is not None:
            self._restore(snapshot)

    def _restore(self, snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
            raise ValueError("Unsupported execution snapshot")
        unknown = set(snapshot) - set(self.state)
        if unknown:
            raise ValueError("Unknown execution snapshot fields")
        required = set(self.state) - {"reality", "consequence"}
        if not required.issubset(snapshot):
            raise ValueError("Incomplete execution snapshot")
        restored = dict(self.state)
        restored.update(snapshot)
        if restored["status"] not in ("idle", "active", "paused", "ended"):
            raise ValueError("Invalid cycle status")
        for name in ("drift_seconds", "recovery_seconds", "quiet_remaining", "next_reminder_at", "gray_hint_remaining"):
            value = restored[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid snapshot duration: " + name)
        if type(restored["stage"]) is not int or not 0 <= restored["stage"] <= 3:
            raise ValueError("Invalid snapshot stage")
        if type(restored["lock_requests"]) is not int or restored["lock_requests"] < 0:
            raise ValueError("Invalid lock request count")
        for name in ("id", "goal", "oath", "reality", "consequence", "last_choice", "last_classification"):
            if not isinstance(restored[name], str):
                raise ValueError("Invalid snapshot text field: " + name)
        if type(restored["return_pending"]) is not bool:
            raise ValueError("Invalid return_pending field")
        # Restart never silently begins monitoring or backfills time spent offline.
        if restored["status"] == "active":
            restored["status"] = "paused"
        restored["recovery_seconds"] = 0.0
        self.state = restored

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)

    def _event(self, kind: str, message: str, **extra: Any) -> dict[str, Any]:
        return {"type": kind, "message": message, "stage": self.state["stage"],
                "session_id": self.state["id"], **extra}

    def start(self, goal: str, oath: str = "", reality: str = "", consequence: str = "") -> list[dict[str, Any]]:
        if not isinstance(goal, str) or not goal.strip():
            return [self._event("error", "请先写下本轮主线。")]
        if any(not isinstance(value, str) for value in (oath, reality, consequence)):
            return [self._event("error", "誓言、现实处境和后果必须是文本。")]
        self.state = _fresh_state()
        self.state.update(status="active", id=uuid.uuid4().hex, goal=goal.strip(), oath=oath,
                          reality=reality, consequence=consequence,
                          next_reminder_at=self.policy.reminder_after_seconds)
        self._discard_next_tick = True
        return [self._event("status", "本轮开始。执行规则已生效。")]

    def pause(self) -> list[dict[str, Any]]:
        if self.state["status"] != "active":
            return []
        self.state["status"] = "paused"
        self.state["recovery_seconds"] = 0.0
        self._discard_next_tick = True
        return [self._event("status", "本轮暂停。暂停时间不会计为偏离。")]

    def resume(self) -> list[dict[str, Any]]:
        if self.state["status"] != "paused":
            return []
        self.state["status"] = "active"
        self._discard_next_tick = True
        return [self._event("status", "本轮恢复。")]

    def end(self) -> list[dict[str, Any]]:
        if self.state["status"] in ("idle", "ended"):
            return []
        self.state["status"] = "ended"
        self.state["recovery_seconds"] = 0.0
        self._discard_next_tick = True
        return [self._event("status", "本轮结束。监督已停止。")]

    def update_policy(self, policy: Policy) -> list[dict[str, Any]]:
        if not isinstance(policy, Policy):
            raise ValueError("Expected a validated Policy")
        self.policy = policy
        # Do not retroactively charge activity under a new rule set.
        self.state["drift_seconds"] = 0.0
        self.state["recovery_seconds"] = 0.0
        self.state["next_reminder_at"] = (policy.reminder_after_seconds if self.state["stage"] == 0
                                         else policy.escalation_after_seconds)
        self._discard_next_tick = True
        return [self._event("status", "规则已更新；偏离计时从新规则生效后重新累计。")]

    def choose(self, action: str, reflection: str = "") -> list[dict[str, Any]]:
        if self.state["status"] != "active":
            return [self._event("error", "当前没有正在执行的周期。")]
        action = action.upper() if isinstance(action, str) else ""
        if action == "BACK":
            self.state["return_pending"] = True
            self.state["last_choice"] = action
            return [self._event("status", "回到主线；连续相关活动达到设定时间后清档。")]
        if action in ("WORTH", "BREATHE"):
            if self.state["stage"] >= self.policy.max_stage:
                return [self._event("error", "第三档仅接受回主线或显式选择锁屏。")]
            self.state["quiet_remaining"] = self.policy.quiet_seconds
            self.state["last_choice"] = action
            self.state["return_pending"] = False
            message = "选择已登记，开始限时静默。" if action == "WORTH" else "喘息已登记，开始限时静默。"
            return [self._event("status", message, choice=action, reflection=str(reflection)[:4000])]
        if action == "LOCK":
            if self.state["stage"] != self.policy.max_stage:
                return [self._event("error", "只有第三档的显式选择可以请求锁屏。")]
            if not self.policy.lock_enabled:
                return [self._event("error", "锁屏策略未启用。")]
            self.state["last_choice"] = action
            self.state["lock_requests"] += 1
            return [self._event("lock_requested", "已收到你的显式锁屏选择。", action_id=uuid.uuid4().hex)]
        return [self._event("error", "未知选择；没有执行任何强制动作。")]

    def _message(self, stage: int) -> str:
        if stage == 1:
            reality = self.state["reality"]
            return (("你写下的处境：\n" + reality + "\n\n") if reality else "") + "最好你现在做的事情是有意义的。\n本轮主线：" + self.state["goal"]
        if stage == 2:
            previous = {"WORTH": "你此前选择了它值得。", "BREATHE": "你此前选择了喘息。",
                        "BACK": "你此前选择了回到主线。"}.get(self.state["last_choice"], "本轮已经提醒过一次。")
            consequence = self.state["consequence"]
            return previous + (("\n你写下的后果：\n" + consequence) if consequence else "") + "\n你仍选择继续吗。\n本轮主线仍是：" + self.state["goal"]
        oath = self.state["oath"]
        return ((oath + "\n\n") if oath else "") + "你忘了你要成为谁了吗。\n回到本轮主线。"

    def tick(self, observation: Observation | dict[str, Any], elapsed_seconds: float) -> list[dict[str, Any]]:
        if self.state["status"] != "active":
            return []
        if isinstance(observation, dict):
            observation = Observation(**observation)
        if not isinstance(observation, Observation):
            raise ValueError("Expected an Observation")
        if self._discard_next_tick:
            self._discard_next_tick = False
            return []
        if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, (float, int)):
            return []
        dt = float(elapsed_seconds)
        if not math.isfinite(dt) or dt <= 0 or dt > self.policy.max_sample_gap_seconds:
            self.state["recovery_seconds"] = 0.0
            self.state["last_classification"] = "UNKNOWN"
            return []
        idle = observation.idle_seconds
        if (observation.status != "ok" or observation.locked or idle is None
                or not isinstance(idle, (int, float)) or not math.isfinite(idle)
                or idle < 0 or idle >= self.policy.idle_threshold_seconds):
            self.state["recovery_seconds"] = 0.0
            self.state["last_classification"] = "UNKNOWN"
            return []
        local = classify(observation.process, observation.title, self.policy)
        self.state["last_classification"] = local
        if local in ("UNKNOWN", "PRIVATE"):
            self.state["recovery_seconds"] = 0.0
            return []
        # An active, observed interval advances quiet timers; sleep and detection gaps do not.
        self.state["quiet_remaining"] = max(0.0, self.state["quiet_remaining"] - dt)
        self.state["gray_hint_remaining"] = max(0.0, self.state["gray_hint_remaining"] - dt)
        related = local == "ON_TASK" or (local == "GRAY" and observation.semantic == "related")
        if related:
            self.state["recovery_seconds"] += dt
            if self.state["recovery_seconds"] >= self.policy.recovery_seconds:
                had_state = self.state["stage"] > 0 or self.state["return_pending"] or self.state["drift_seconds"] > 0
                self.state.update(stage=0, drift_seconds=0.0, quiet_remaining=0.0,
                                  return_pending=False, last_choice="", next_reminder_at=self.policy.reminder_after_seconds)
                if had_state:
                    return [self._event("recovered", "连续相关活动已达到设定时间，本轮档位清零。")]
            return []
        self.state["recovery_seconds"] = 0.0
        if local == "GRAY":
            if (observation.semantic == "unrelated" and self.state["quiet_remaining"] <= 0
                    and self.state["gray_hint_remaining"] <= 0):
                self.state["gray_hint_remaining"] = self.policy.quiet_seconds
                return [self._event("gray_hint", "语义建议：当前窗口可能与本轮主线无关。请自行判断；本次不计强制档位。")]
            return []
        self.state["drift_seconds"] += dt
        if (self.state["quiet_remaining"] > 0 or self.state["stage"] >= self.policy.max_stage
                or self.state["drift_seconds"] < self.state["next_reminder_at"]):
            return []
        self.state["stage"] += 1
        self.state["next_reminder_at"] = self.state["drift_seconds"] + self.policy.escalation_after_seconds
        return [self._event("reminder", self._message(self.state["stage"]), classification="DRIFT")]


def _atomic_json(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=destination.name + ".", suffix=".tmp", delete=False) as handle:
            name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, destination)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def save_snapshot(path: str | Path, snapshot: dict[str, Any]) -> None:
    # Validation is explicit and does not silently repair or reset damaged state.
    Engine(snapshot=snapshot)
    _atomic_json(path, snapshot)


def load_snapshot(path: str | Path) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        snapshot = json.load(handle)
    Engine(snapshot=snapshot)
    return snapshot


def save_policy(path: str | Path, policy: Policy) -> None:
    _atomic_json(path, Policy.from_dict(policy.to_dict()).to_dict())


def load_policy(path: str | Path) -> Policy:
    path = Path(path)
    if not path.exists():
        return Policy()
    with path.open("r", encoding="utf-8") as handle:
        return Policy.from_dict(json.load(handle))
