"""决策层的上下文窗口：历史帧用**结构摘要**承载，只保留最近几张完整图。

工单 M10 AC 5：**成本主控杆是历史上下文里的截图长度**，不是模型单价。
所以这里的规矩是：

* **完整截图只留最近 2~4 张**（:class:`ContextWindow` 的 ``keep_frames``）；
* 更早的帧压成一行**结构摘要**（尺寸 + 截图哈希 + 那一轮发生了什么），
  摘要进上下文、图像不进；
* 每轮的标准输出与动作结果也进摘要 —— 那是回灌给模型的东西。

> **词汇**：「结构摘要」= 用文字结构代替完整截图。
"""

from __future__ import annotations

from dataclasses import dataclass

from minicode.action.ports import CapturedFrame

__all__ = ["FrameNote", "ContextWindow"]

#: 完整截图保留张数的下限 / 上限（工单 M10 AC 5：2~4 张）。
KEEP_FRAMES_MIN = 2
KEEP_FRAMES_MAX = 4


@dataclass(frozen=True)
class FrameNote:
    """一帧被淘汰后留下的结构摘要。"""

    index: int
    width: int
    height: int
    sha256: str
    note: str

    def render(self) -> str:
        return (
            f"第{self.index}帧 {self.width}×{self.height} "
            f"sha256={self.sha256[:12]}｜{self.note}"
        )


class ContextWindow:
    """决策层的历史上下文。"""

    def __init__(self, *, keep_frames: int = 3):
        if not KEEP_FRAMES_MIN <= keep_frames <= KEEP_FRAMES_MAX:
            raise ValueError(
                "完整截图只保留最近 "
                f"{KEEP_FRAMES_MIN}~{KEEP_FRAMES_MAX} 张（工单 M10 AC 5），"
                f"收到 {keep_frames}"
            )
        self.keep_frames = int(keep_frames)
        #: 每一轮的结构摘要（文本）。
        self._summaries: list[str] = []
        #: 最近几帧（图像）与它们那一轮的摘要。
        self._frames: list[tuple[CapturedFrame, str]] = []
        #: 更早的帧留下的结构摘要。
        self._notes: list[FrameNote] = []
        self._index = 0

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def add_round(self, *, summary: str, frame: CapturedFrame | None) -> None:
        """记一轮：结构摘要必留，图像按张数上限淘汰。"""
        self._index += 1
        self._summaries.append(summary)
        if frame is None:
            return
        self._frames.append((frame, summary))
        while len(self._frames) > self.keep_frames:
            old_frame, old_summary = self._frames.pop(0)
            self._notes.append(
                FrameNote(
                    index=self._index - len(self._frames),
                    width=old_frame.width,
                    height=old_frame.height,
                    sha256=old_frame.digest(),
                    note=old_summary,
                )
            )

    def add_note(self, note: str) -> None:
        """加一行不属于某一轮的补充（反思步的产出走这里）。"""
        self._summaries.append(note)

    # ------------------------------------------------------------------
    # 读出
    # ------------------------------------------------------------------

    def render(self) -> str:
        """渲染成给模型看的文本上下文。"""
        lines = [f"已经跑过的轮次：{len(self._summaries)}"]
        lines.extend(f"- {summary}" for summary in self._summaries)
        if self._notes:
            lines.append("更早的完整图已被压成结构摘要（图像不再重发）：")
            lines.extend(f"- {note.render()}" for note in self._notes)
        return "\n".join(lines)

    def frames(self) -> tuple[CapturedFrame, ...]:
        """最近保留的那几张完整图（按时间顺序）。"""
        return tuple(frame for frame, _ in self._frames)

    @property
    def notes(self) -> tuple[FrameNote, ...]:
        return tuple(self._notes)

    @property
    def dropped_frames(self) -> int:
        """被淘汰成结构摘要的帧数。"""
        return len(self._notes)

    @property
    def kept_frames(self) -> int:
        return len(self._frames)

    @property
    def rounds(self) -> int:
        return len(self._summaries)
