"""**动作日志的回放**（工单 M17 AC 2）。

回放做两件事：

1. **重建每一步的动作** —— 从日志把动作种类与参数**还原成协议里的 `Action`
   对象**（`reconstruct_action`）；重建不出来 = 这份日志不够回放；
2. **用独立判据核对** —— 把日志里的字段**重新算一遍**，跟它自己对不上就点名
   （字段完整性 / 结果自洽 / 三类失败归类 / 失败类型 / 时间顺序 / 三段耗时占比 /
   落点落在目标控件框内）。

⚠️ **回放是动作日志层面的可回溯**，不是「录一段屏再回放」
（``docs/spec.md`` §测试决策：明确不做全盘录制回放）。
本模块**不碰桌面**：它读日志、重建动作、核对记录。

另有一个**可选**的 ``execute`` 钩子：把重建出来的动作**再执行一遍**，
执行结果与记录不一致也点名。⚠️ 钩子由**调用方**接到受控环境里 ——
本模块自己不碰真桌面。

⚠️ **输入文本的动作无法忠实回放**：那一步的正文**不进日志**
（C-15：界面内容不进日志），所以重建出来的动作没有正文。
这是**有意为之**，不是漏了。

字段完整性的判据
----------------

工单 AC1 点名七样东西：时间、动作类型、目标窗口句柄与标题、落点坐标、
截图哈希、动作前后状态、结果（含错误码）；另加两项结构性的（抓图尺寸 /
三类失败）。判据分两层：

* **键必须在** —— 一样不少；
* **值要在** —— 该有的值不许是空的，而且**按动作种类判**：
  需要落点的动作必须有落点、不需要落点的动作必须没有落点；
  选中过目标的必须有窗口句柄与标题；跑过校验的必须有前后状态。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from minicode.action.errors import ErrorCode, failure_type_of
from minicode.action.protocol import (
    OBSERVATION_KINDS,
    POINT_KINDS,
    STANDALONE_KINDS,
    Action,
    ActionKind,
    ActionResult,
    parse_click_params,
)
from minicode.action.records import FAILURE_FIELDS, POINT_SPACE, stage_share

__all__ = [
    "ReplayStep",
    "ReplayReport",
    "missing_fields",
    "reconstruct_action",
    "replay_records",
]

#: 记录里**必须有的键**（缺一个即不合格）。
_REQUIRED_KEYS: tuple[tuple[str, str], ...] = (
    ("timestamp", "时间"),
    ("action", "动作类型"),
    ("target", "目标"),
    ("point", "落点坐标"),
    ("capture", "抓图尺寸"),
    ("screenshot_sha256", "截图哈希"),
    ("state", "动作前后状态"),
    ("result", "结果"),
    ("failures", "三类失败"),
)

#: 协议里的动作种类（值 → 名字）。
_KIND_VALUES = {kind.value for kind in ActionKind}
#: **要落点的**动作种类：需要落点的（`POINT_KINDS`）+ 观察型（`OBSERVATION_KINDS`
#: —— 放大产出一个落点，只是不注入任何输入）。
_LANDING_KIND_VALUES = {kind.value for kind in POINT_KINDS | OBSERVATION_KINDS}
_STANDALONE_KIND_VALUES = {kind.value for kind in STANDALONE_KINDS}
#: 协议里的错误码（值集合）。
_ERROR_CODE_VALUES = {code.value for code in ErrorCode}


@dataclass(frozen=True)
class ReplayStep:
    """回放出来的一步（**从日志重建**，不是抄记录）。"""

    index: int
    timestamp: str
    action_kind: str
    intent: str
    target_name: str | None
    point: tuple[int, int] | None
    ok: bool
    error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "action": self.action_kind,
            "intent": self.intent,
            "target_name": self.target_name,
            "point": list(self.point) if self.point else None,
            "ok": self.ok,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class ReplayReport:
    """回放报告：每一步 + 对不上的地方。"""

    steps: tuple[ReplayStep, ...]
    problems: tuple[str, ...] = ()

    @property
    def consistent(self) -> bool:
        """**回放结果与当时的记录一致**吗。"""
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [step.to_dict() for step in self.steps],
            "problems": list(self.problems),
            "consistent": self.consistent,
        }


def missing_fields(record: Mapping[str, Any]) -> list[str]:
    """这条记录缺哪些字段（**空 = 字段完整**）。

    键缺失与「该有的值没有」都算 —— 工单 AC1 写的是「缺字段即不合格」。
    """
    missing: list[str] = []
    for key, label in _REQUIRED_KEYS:
        if key not in record:
            missing.append(label)
    if missing:
        return missing

    result = record.get("result") or {}
    for key, label in (("ok", "结果.ok"), ("error_code", "结果.error_code")):
        if key not in result:
            missing.append(label)
    target = record.get("target") or {}
    for key, label in (("window_handle", "目标窗口句柄"), ("window_title", "目标窗口标题")):
        if key not in target:
            missing.append(label)
    state = record.get("state") or {}
    for key, label in (("before", "动作前状态"), ("after", "动作后状态")):
        if key not in state:
            missing.append(label)
    if missing:
        return missing

    if not str(record.get("timestamp") or "").strip():
        missing.append("时间")

    kind = str(record.get("action") or "")
    point = record.get("point")
    if kind and kind not in _KIND_VALUES:
        missing.append("动作类型")
    elif not kind and _action_attempted(record):
        # 动作下发过却没有动作类型 = 日志缺了「它当时想干什么」。
        missing.append("动作类型")
    if kind in _LANDING_KIND_VALUES:
        if not isinstance(point, Mapping) or point.get("x") is None or point.get("y") is None:
            missing.append("落点坐标")
        elif point.get("space") != POINT_SPACE:
            missing.append("落点坐标空间")
    elif kind in _STANDALONE_KIND_VALUES and point is not None:
        missing.append("落点坐标")

    capture = record.get("capture") or {}
    digest = record.get("screenshot_sha256")
    if capture.get("width") is not None:
        if not isinstance(digest, str) or len(digest) != 64:
            missing.append("截图哈希")
    elif digest is not None:
        missing.append("截图哈希")

    if target.get("name") is not None:
        if target.get("window_handle") is None:
            missing.append("目标窗口句柄")
        if not target.get("window_title"):
            missing.append("目标窗口标题")

    if record.get("verification_result") is not None:
        if state.get("before") is None:
            missing.append("动作前状态")
        if state.get("after") is None:
            missing.append("动作后状态")

    if not isinstance(result.get("ok"), bool):
        missing.append("结果.ok")
    elif not result.get("ok") and not result.get("error_code"):
        # 失败必须带错误码（结果里没有码 = 这一步为什么失败查不出来）。
        missing.append("结果.error_code")

    return missing


def reconstruct_action(record: Mapping[str, Any]) -> Action | None:
    """从记录**重建**那个动作（重建不出来 = 这份日志不够回放）。"""
    kind = str(record.get("action") or "")
    try:
        action_kind = ActionKind(kind)
    except ValueError:
        return None
    params = record.get("params")
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        return None
    if action_kind is ActionKind.CLICK:
        try:
            button, count = parse_click_params(params)
        except ValueError:
            return None
        params = {**params, "button": button, "click_count": count}
    return Action(
        kind=action_kind,
        params=dict(params),
        intent=str(record.get("intent") or ""),
    )


def replay_records(
    records: Sequence[Mapping[str, Any]],
    *,
    execute: Callable[[Action], ActionResult] | None = None,
) -> ReplayReport:
    """回放一份动作日志。

    ``execute``
        可选：把重建出来的动作**再执行一遍**的回调（返回动作结果）。
        给了它，回放结果与记录不一致的地方会被点名。
    """
    steps: list[ReplayStep] = []
    problems: list[str] = []
    previous_time: datetime | None = None

    for index, record in enumerate(records):
        label = f"第 {index + 1} 步"
        for name in missing_fields(record):
            problems.append(f"{label}缺字段：{name}")

        action = reconstruct_action(record)
        if action is None and _action_attempted(record):
            kind = str(record.get("action") or "")
            if kind in _KIND_VALUES:
                problems.append(f"{label}的动作参数重建不出来（{kind}）")
            else:
                problems.append(f"{label}的动作类型不在协议里（{kind}）")

        result = record.get("result") or {}
        ok = bool(result.get("ok"))
        code = result.get("error_code")
        code = str(code) if code else None

        point = record.get("point")
        landing = (
            (int(point["x"]), int(point["y"]))
            if isinstance(point, Mapping) and point.get("x") is not None
            else None
        )
        target = record.get("target") or {}
        steps.append(
            ReplayStep(
                index=index,
                timestamp=str(record.get("timestamp") or ""),
                action_kind=str(record.get("action") or ""),
                intent=str(record.get("intent") or ""),
                target_name=target.get("name"),
                point=landing,
                ok=ok,
                error_code=code,
            )
        )

        problems.extend(_result_problems(label, record, ok, code))
        problems.extend(_timing_problems(label, record))
        problems.extend(_landing_problems(label, record))

        moment = _parse_timestamp(str(record.get("timestamp") or ""))
        if moment is not None and previous_time is not None and moment < previous_time:
            problems.append(f"{label}的时间戳比上一步还早（步骤顺序乱了）")
        if moment is not None:
            previous_time = moment

        if execute is not None and action is not None:
            problems.extend(_replay_problems(label, execute(action), ok, code))

    return ReplayReport(steps=tuple(steps), problems=tuple(problems))


def _action_attempted(record: Mapping[str, Any]) -> bool:
    """这一步**下发过动作**吗。

    ⚠️ **定位阶段就失败的步骤没有下发任何动作**（动作压根没构造出来）——
    那种记录里没有动作类型是**如实**的，不是缺字段。
    反过来：动作下发过却没有动作类型 = 日志缺了「它当时想干什么」。
    """
    if str(record.get("action") or ""):
        return True
    failures = record.get("failures") or {}
    return not failures.get("locate")


def _result_problems(
    label: str, record: Mapping[str, Any], ok: bool, code: str | None
) -> list[str]:
    """结果自洽性：结果、错误码、三类失败、失败类型。"""
    problems: list[str] = []
    if ok and code is not None:
        problems.append(f"{label}的结果自相矛盾：成功却带着错误码 {code}")
    if not ok and code is None:
        problems.append(f"{label}的结果缺错误码（失败必须有码）")

    known = _ERROR_CODE_VALUES
    if code is not None and code not in known:
        problems.append(f"{label}的错误码不在协议里：{code}")

    failures = record.get("failures") or {}
    classified = [name for name in FAILURE_FIELDS if failures.get(name)]
    if ok:
        if classified:
            problems.append(f"{label}成功却记了失败：{'、'.join(classified)}")
    elif len(classified) != 1:
        problems.append(f"{label}的失败没有归进三类失败之一（记了：{classified or '没有'}）")
    elif failures.get(classified[0]) != code:
        problems.append(f"{label}的失败归类与错误码对不上")

    # 失败类型是**定位阶段**那一维的分类：只在定位失败时才与错误码对齐。
    if code is not None and code in known and failures.get("locate"):
        expected = failure_type_of(ErrorCode(code)).value
        if record.get("failure_type") != expected:
            problems.append(f"{label}的失败类型与错误码对不上（应为 {expected}）")
    return problems


def _timing_problems(label: str, record: Mapping[str, Any]) -> list[str]:
    """三段耗时的**占比**必须与那三个值对得上（占比是算出来的，不是另一份数据）。"""
    timings = record.get("timings_ms") or {}
    share = record.get("timings_share") or {}
    try:
        expected = stage_share(
            timings.get("locate", 0.0),
            timings.get("decision", 0.0),
            timings.get("execute_verify", 0.0),
        )
    except (TypeError, ValueError):
        return [f"{label}的三段耗时读不出来"]
    for name, value in expected.items():
        actual = share.get(name)
        if actual is None or abs(float(actual) - value) > 1e-9:
            return [f"{label}的三段耗时占比与实测值对不上（{name}）"]
    return []


def _landing_problems(label: str, record: Mapping[str, Any]) -> list[str]:
    """落点必须落在**记录里的目标控件框**内。

    这条是**从日志自己重算**的：落点与控件框都是日志里的字段，
    两者的关系不必信任何外部说法。控件在定位与执行之间挪过 → 这里会报出来。
    """
    point = record.get("point")
    rect = (record.get("target") or {}).get("rect")
    if not isinstance(point, Mapping) or not isinstance(rect, (list, tuple)):
        return []
    if len(rect) != 4 or point.get("x") is None or point.get("y") is None:
        return []
    left, top, right, bottom = (int(value) for value in rect)
    x, y = int(point["x"]), int(point["y"])
    if left <= x < right and top <= y < bottom:
        return []
    return [f"{label}的落点落在目标控件框外（落点=({x}, {y}) 控件框={list(rect)}）"]


def _replay_problems(
    label: str, outcome: Any, ok: bool, code: str | None
) -> list[str]:
    """回放出来的结果与记录对不上 → 点名。"""
    replayed_ok = bool(getattr(outcome, "ok", False))
    replayed_code = getattr(outcome, "error_code", None)
    replayed_code = getattr(replayed_code, "value", replayed_code)
    replayed_code = str(replayed_code) if replayed_code else None
    if replayed_ok != ok or replayed_code != code:
        return [
            f"{label}的回放结果与记录不一致："
            f"回放 {replayed_ok}/{replayed_code}，记录 {ok}/{code}"
        ]
    return []


def _parse_timestamp(text: str) -> datetime | None:
    """解析时间戳（读不出来就返回 ``None`` —— 不猜）。"""
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
