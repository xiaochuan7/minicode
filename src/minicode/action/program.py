"""``execute_code`` 动作的**受控执行环境**（工单 M10）。

动作程序是决策层唯一的产出形态（ADR-0005）。本模块回答一个问题：
**这段程序在什么环境里跑、能做什么**。

规矩（工单 M10 AC 6）：

* 程序**只能调用动作协议暴露的动作**与定位接口 —— 暴露的名字是
  :data:`PROGRAM_API_NAMES` 里那一个白名单，多给一个名字就拒；
* **任意系统调用与任意文件写入被拒**：``import`` / ``open`` / ``eval`` /
  ``exec`` / ``__import__`` / 双下划线属性访问等在**执行前**就被静态拒绝，
  并给出明确理由（不静默放过，也不靠运行时才炸）；
* 程序的标准输出、标准错误与异常都被**收进结果**（决策层要回灌给模型）；
* 程序跑太久（行数超限）也停 —— 死循环不许把决策层挂死。

**判定与执行的边界**：静态扫描只负责「这段程序能不能跑」，
动作本身由动作执行器与动作执行缝负责（那里才有端口）。
"""

from __future__ import annotations

import ast
import io
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import Any

from .errors import ErrorCode

__all__ = [
    "PROGRAM_API_NAMES",
    "FORBIDDEN_CALLS",
    "ProgramLimits",
    "ProgramOutcome",
    "ProgramRejected",
    "scan_program",
    "run_program",
]


#: 程序**可以调用**的名字白名单：动作协议暴露的动作 + 定位 + 标准输出。
#:
#: 只有这些名字会被放进程序的作用域；别的名字程序里根本不存在
#: （连 ``open`` 都不存在 —— 它在静态扫描阶段就被拒了）。
PROGRAM_API_NAMES: frozenset[str] = frozenset(
    {
        "click",  # 点击一个目标（定位 + 执行）
        "find",  # 只定位，不动作
        "type_text",  # 输入文本
        "press_key",  # 按键
        "wait",  # 等待
        "screenshot",  # 截图（只给摘要，不给图像数据）
        "ask_user",  # 请求人工（把任务交还给人）
    }
)

#: 程序编译时用的**文件名**（受控环境里程序没有真实路径）。
#:
#: 它是**行数计数器**的判据（只数程序自己的行，见 :class:`_LineCounter`）。
_PROGRAM_FILENAME = "<action-program>"

#: **禁止调用**的名字：系统调用、文件写入、代码执行与反射的入口。
#:
#: ``import`` 语句、双下划线属性访问与**含双下划线的字符串常量**由
#: :func:`scan_program` 单独判（后者堵的是 ``"{0.__class__}".format(x)``
#: 这类**运行期**才解析属性的路径）。
FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "exit",
        "quit",
        "help",
        "memoryview",
    }
)

#: 程序里**允许**用到的内置函数。
#:
#: 白名单而不是黑名单：白名单之外的内置函数一律不存在。
_ALLOWED_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "frozenset": frozenset,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "print": print,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "ZeroDivisionError": ZeroDivisionError,
}


@dataclass(frozen=True)
class ProgramLimits:
    """程序执行的规模上限（默认值是工程值，不是实测值）。"""

    #: 最多执行多少行（死循环的兜底）。**只数程序自己的行**（见
    #: :class:`_LineCounter`）—— 框架干了多少活不占这个预算。
    max_lines: int = 20000
    #: 标准输出 / 标准错误各最多留多少字符。
    max_output_chars: int = 8000


@dataclass(frozen=True)
class ProgramOutcome:
    """一次程序执行的结果。

    ``ok`` 只表示「程序跑完了且没抛异常」—— **不代表任务完成**
    （协议里没有「完成」这个动作，终止由决策层的停止条件承载）。

    程序产生过哪些动作、要不要交还给人，由**接口实现那一侧**记
    （决策层知道每个接口调用做了什么）。
    """

    ok: bool
    stdout: str = ""
    stderr: str = ""
    error_code: ErrorCode | None = None
    error_message: str | None = None


class ProgramRejected(Exception):
    """程序在**执行前**被拒（静态扫描没过）。"""

    def __init__(self, reasons: Sequence[str]):
        super().__init__("；".join(reasons))
        self.reasons: tuple[str, ...] = tuple(reasons)


class _LimitExceeded(Exception):
    """程序执行的行数超限。"""


def scan_program(source: str) -> list[str]:
    """静态扫描一段程序，返回**拒绝理由**列表（空列表 = 放行）。

    判据（工单 M10 AC 6）：

    * 不许 ``import`` / ``from ... import``（系统调用的入口）；
    * 不许调用 :data:`FORBIDDEN_CALLS` 里的名字（``open`` / ``eval`` / …）；
    * 不许访问**双下划线属性**（``__class__`` / ``__globals__`` /
      ``__subclasses__`` 是逃出沙箱的经典路径）；
    * 不许**含双下划线的字符串常量** —— 那是属性访问的**运行期**写法
      （``"{0.__class__}".format(x)`` 在 AST 里看不见属性节点，
      但运行期会解析出 ``__class__``）。动作程序里不需要这种串。

    ⚠️ **这不是操作系统级隔离**：白名单 + 静态扫描挡的是「程序直接做坏事」，
    不是「程序千方百计地逃出去」。真要把动作程序当不可信代码跑，
    得另做进程级隔离（后票）。
    """
    problems: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"程序语法错误：第 {exc.lineno} 行 {exc.msg}"]

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            problems.append(f"第 {node.lineno} 行：不允许 import（系统调用与文件写入的入口）")
            continue
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                problems.append(
                    f"第 {node.lineno} 行：不允许调用 {func.id}()"
                    "（任意系统调用与任意文件写入都被拒）"
                )
        if isinstance(node, ast.Attribute):
            name = node.attr
            if name.startswith("__") and name.endswith("__"):
                problems.append(
                    f"第 {node.lineno} 行：不允许访问双下划线属性 {name}（沙箱逃逸路径）"
                )
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_CALLS:
            problems.append(f"第 {node.lineno} 行：不允许引用 {node.id}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "__" in node.value:
                problems.append(
                    f"第 {node.lineno} 行：字符串里不允许出现双下划线"
                    "（运行期属性访问的沙箱逃逸路径）"
                )
    return problems


def run_program(
    source: str,
    api: Mapping[str, Callable[..., Any]],
    *,
    limits: ProgramLimits | None = None,
) -> ProgramOutcome:
    """在受控环境里跑一段动作程序。

    * 静态扫描没过 → **执行前**拒绝（:attr:`ProgramOutcome.error_code` =
      ``program_rejected``），一个动作都不下发；
    * 跑完 → 标准输出 / 标准错误 / 异常都进结果，交给决策层回灌给模型。
    """
    limits = limits or ProgramLimits()

    unknown = sorted(set(api) - PROGRAM_API_NAMES)
    if unknown:
        return ProgramOutcome(
            ok=False,
            error_code=ErrorCode.PROGRAM_REJECTED,
            error_message=(
                "动作程序只能调用动作协议暴露的动作；"
                f"多出来的名字：{', '.join(unknown)}"
            ),
        )

    problems = scan_program(source)
    if problems:
        return ProgramOutcome(
            ok=False,
            error_code=ErrorCode.PROGRAM_REJECTED,
            error_message="动作程序被拒：" + "；".join(problems),
        )

    env: dict[str, Any] = {
        "__name__": "minicode_action_program",
        "__builtins__": dict(_ALLOWED_BUILTINS),
    }
    env.update(api)

    stdout = _LimitedWriter(limits.max_output_chars)
    stderr = _LimitedWriter(limits.max_output_chars)
    counter = _LineCounter(limits.max_lines)
    previous_trace = sys.gettrace()
    sys.settrace(counter)
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(compile(source, _PROGRAM_FILENAME, "exec"), env, env)  # noqa: S102
    except _LimitExceeded as exc:
        return ProgramOutcome(
            ok=False,
            stdout=stdout.text(),
            stderr=stderr.text(),
            error_code=ErrorCode.PROGRAM_REJECTED,
            error_message=f"动作程序超出规模上限：{exc}",
        )
    except BaseException as exc:  # 程序自己的异常：如实回传，不静默
        return ProgramOutcome(
            ok=False,
            stdout=stdout.text(),
            stderr=stderr.text(),
            error_code=ErrorCode.EXECUTE_FAILED,
            error_message=f"{type(exc).__name__}: {exc}",
        )
    finally:
        sys.settrace(previous_trace)

    return ProgramOutcome(
        ok=True,
        stdout=stdout.text(),
        stderr=stderr.text(),
    )

class _LimitedWriter(io.TextIOBase):
    """只留前 N 个字符的输出缓冲（超出部分只记一次「已截断」）。"""

    def __init__(self, limit: int):
        self._limit = int(limit)
        self._buffer = io.StringIO()
        self._truncated = False

    def write(self, text: str) -> int:
        if self._buffer.tell() >= self._limit:
            self._truncated = True
            return len(text)
        self._buffer.write(text)
        return len(text)

    def text(self) -> str:
        body = self._buffer.getvalue()
        if self._truncated:
            body += "\n…（输出已截断）"
        return body


class _LineCounter:
    """行数计数器：超过上限就中断程序。

    ⚠️ **只数程序自己的行**（编译时那个文件名）。
    这个上限是「**模型写的那段程序**的死循环兜底」，**不是**
    「框架这一轮干了多少活」的预算 —— 工单 M16 轮实测：把框架的行也算进去时，
    同一段程序会**因为框架多干了点活**（多问了一次模型、多走了一遍审批）
    而被报成「规模上限」—— 那是**静默失败**：模型背不了这个锅，
    而且同样的输入可能这次过、下次不过。
    """

    def __init__(self, limit: int, filename: str = _PROGRAM_FILENAME):
        self._limit = int(limit)
        self._filename = filename
        self._count = 0

    def __call__(self, frame, event, arg):  # noqa: ANN001 - sys.settrace 的签名
        if event == "line" and frame.f_code.co_filename == self._filename:
            self._count += 1
            if self._count > self._limit:
                raise _LimitExceeded(f"执行行数超过 {self._limit} 行")
        return self
