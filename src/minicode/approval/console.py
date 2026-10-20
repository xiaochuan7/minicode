"""**在命令行里问人**（工单 M16 的「人工批准」入口）。

审批请求里有**意图**、**目标**与**dry-run 的对象**（控件框、位置、截图哈希）——
这份东西要**给人看**，人才判得了。这一模块把它打印出来，再问一句批不批。

⚠️ **用词**：这一套叫「审批」，**不叫「确认」**
（`CONTEXT.md` 的「审批」词条把「确认 / 许可」列为 avoid 词）。

⚠️ **非交互一律不批**（fail-closed）：stdin 不是终端、或者人没回话 →
**按未获批处理**。绝不把「没人回答」当成「批准」。
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from .gate import ApprovalGate
from .ports import SensitiveWindowPolicy
from .request import ApprovalDecision, ApprovalRequest

__all__ = ["ConsoleApprover", "APPROVE_WORDS", "console_gate"]

#: 算「批准」的回话（大小写无关）。
APPROVE_WORDS = ("y", "yes", "是", "批准", "ok")


class ConsoleApprover:
    """把审批请求打到标准输出，问人一句。"""

    def __init__(
        self,
        *,
        reader: Callable[[str], str] | None = None,
        writer: Callable[[str], None] | None = None,
        interactive: Callable[[], bool] | None = None,
        stdout=None,
    ):
        self._reader = reader
        self._writer = writer
        self._interactive = interactive
        #: **真实的标准输出**（构造时抓一份）。
        #:
        #: ⚠️ 决策层跑动作程序时会把 ``stdout`` **重定向**进程序输出
        #: （那段输出还会**回灌给模型**）—— 审批请求是**给人看的**，
        #: 绝不能被重定向吞掉，也绝不能回灌进模型上下文。
        self._stdout = stdout if stdout is not None else sys.stdout

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        self._write(request.to_text())
        if not self._is_interactive():
            self._write("（当前不是交互式终端，没有人能批这一条）→ 未获批")
            return ApprovalDecision(
                approved=False,
                request_id=request.request_id,
                reason="非交互式环境，没有人能批这一条",
            )
        answer = self._read().strip().lower()
        if answer in APPROVE_WORDS:
            return ApprovalDecision(
                approved=True, request_id=request.request_id, reason="人工批准"
            )
        return ApprovalDecision(
            approved=False, request_id=request.request_id, reason="人工未批准"
        )

    def _write(self, text: str) -> None:
        if self._writer is not None:
            self._writer(text)
            return
        print(text, file=self._stdout)

    def _read(self) -> str:
        if self._reader is not None:
            return self._reader("")
        self._stdout.write("批准这一条吗？[y/N] ")
        self._stdout.flush()
        line = sys.stdin.readline()
        return line if line else ""

    def _is_interactive(self) -> bool:
        if self._interactive is not None:
            return bool(self._interactive())
        try:
            return bool(sys.stdin is not None and sys.stdin.isatty())
        except (AttributeError, ValueError):
            return False


def console_gate(
    *,
    sensitive_policy: SensitiveWindowPolicy | None = None,
    read_foreground_window: bool = True,
) -> ApprovalGate:
    """**真机路径的默认闸门**：命令行里问人。

    * 审批人 = :class:`ConsoleApprover`（非交互一律不批）；
    * 「截含敏感窗口的屏」= 当前**前台窗口**的标题命中敏感词表
      —— ⚠️ **敏感窗口的词表归 M20**（`docs/tickets/README.md`：
      「M20 管『哪些窗口根本不许拍 / 不许发』」），因此这里**默认不接策略**：
      没接策略就没有「敏感窗口」这个概念（判定留给 M20 接进来时做）。

    ⚠️ ``read_foreground_window=False`` = 不读真窗口（假桌面上用：
    那条路上拍的是假画面，问真窗口没有意义）。
    ⚠️ 这一条**不产生**任何网络访问，也不读仓库 ``.env`` 之外的凭据。
    """
    from .windows import WindowsWindowInspector

    return ApprovalGate(
        approver=ConsoleApprover(),
        window_inspector=WindowsWindowInspector() if read_foreground_window else None,
        sensitive_policy=sensitive_policy,
    )
