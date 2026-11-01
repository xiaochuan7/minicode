"""**急停感知注入器**（护栏 1 的执行手 + 护栏 10 的 dry-run 通道）。

包在真注入器外面，实现同一个 :class:`minicode.action.ports.InputInjector`
端口——缝与执行器看到的还是「一个注入器」，不知道外面多了护栏：

* **急停**（护栏 1）：每次注入前查急停事件；已触发 → 先**松开所有按着的
  键与键钮**（防止 Ctrl 卡死之类），再抛
  :class:`minicode.guard.halt.EstopTriggered`（缝外转成 ``emergency_stopped``）。
* **dry-run**（护栏 10）：``dry_run=True`` 时逐条**打印**将下发的动作但不执行；
  注入序列照样记录（回放/审计用），只是不碰真桌面。
* **只读操作放行**：``read_cursor`` / ``read_clipboard`` /
  ``has_non_text_clipboard_content`` / ``probe_injection`` 不是输入，
  急停后也不拦（probe 是无害探针）。
"""

from __future__ import annotations

from typing import Any

from minicode.action.ports import InputInjector

from .estop import EstopWatchdog

__all__ = ["GuardedInjector", "EstopTriggered"]


class EstopTriggered(Exception):
    """急停触发（注入器在释放已按键后抛出；缝外转协议错误码）。"""


class GuardedInjector:
    """急停感知 + 可 dry-run 的注入器（包装 ``InputInjector`` 端口）。"""

    def __init__(
        self,
        inner: InputInjector,
        *,
        estop: EstopWatchdog | None = None,
        dry_run: bool = False,
        printer: Any = print,
    ):
        self._inner = inner
        self._estop = estop
        self._dry_run = dry_run
        self._printer = printer
        #: 注入序列（与 :class:`minicode.action.fakes.FakeInputInjector` 同口径：
        #: ``(method, args)`` 元组列表 —— 断言「零动作下发」就数它）。
        self.journal: list[tuple[str, tuple[Any, ...]]] = []
        self._pressed_buttons: list[str] = []

    # ------------------------------------------------------------------
    # 护栏
    # ------------------------------------------------------------------

    def _halted(self) -> bool:
        return self._estop is not None and self._estop.triggered

    def _release_pressed(self) -> None:
        """把所有**还按着**的鼠标键松开（急停的后半段：不留悬空的键钮）。

        ⚠️ ``press_combo`` / ``type_unicode`` 在真实实现里是**原子**的
        （按下 + 松开一次下发），不存在「按住的键」；
        会悬空的只有 ``button_down`` 之后还没 ``button_up`` 的鼠标键
        （拖拽序列中间急停的情形）。
        """
        for button in reversed(self._pressed_buttons):
            self._inner.button_up(button)
        self._pressed_buttons.clear()

    def _check_before_input(self, method: str, *args: Any) -> bool:
        """注入前置检查。返回 ``True`` = 放行（继续调内层）；``False`` = dry-run 拦下。"""
        self.journal.append((method, args))
        if self._dry_run:
            shown = ", ".join(repr(a) for a in args)
            self._printer(f"[dry-run] {method}({shown}) —— 未执行（护栏 10）")
            return False
        if self._halted():
            self._release_pressed()
            raise EstopTriggered("急停热键已触发：停止一切输入注入")
        return True

    # ------------------------------------------------------------------
    # InputInjector 端口（输入类操作全走 _check_before_input）
    # ------------------------------------------------------------------

    def input_struct_size(self) -> int:
        return self._inner.input_struct_size()

    def probe_injection(self) -> int:
        """无害探针：dry-run 也真实执行（它本来就什么都不点）。"""
        return self._inner.probe_injection()

    def move(self, x: int, y: int) -> None:
        if self._check_before_input("move", x, y):
            self._inner.move(x, y)

    def button_down(self, button: str = "left") -> None:
        if self._check_before_input("button_down", button):
            self._inner.button_down(button)
            if button not in self._pressed_buttons:
                self._pressed_buttons.append(button)

    def button_up(self, button: str = "left") -> None:
        if self._check_before_input("button_up", button):
            self._inner.button_up(button)
            if button in self._pressed_buttons:
                self._pressed_buttons.remove(button)

    def scroll_delta(self, x: int, y: int, delta: int) -> None:
        if self._check_before_input("scroll_delta", x, y, delta):
            self._inner.scroll_delta(x, y, delta)

    def press_combo(self, combo: tuple[str, ...]) -> None:
        if self._check_before_input("press_combo", combo):
            self._inner.press_combo(combo)

    def type_unicode(self, text: str) -> None:
        if self._check_before_input("type_unicode", text):
            self._inner.type_unicode(text)

    def read_cursor(self) -> tuple[int, int] | None:
        """只读：急停后照样放行（松键需要知道现状，且它不输入）。"""
        return self._inner.read_cursor()

    def read_clipboard(self) -> str | None:
        """只读：不拦。"""
        return self._inner.read_clipboard()

    def write_clipboard(self, text: str | None) -> None:
        if self._check_before_input("write_clipboard", text):
            self._inner.write_clipboard(text)

    def has_non_text_clipboard_content(self) -> bool:
        """只读：不拦。"""
        return self._inner.has_non_text_clipboard_content()
