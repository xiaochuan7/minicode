"""引用纪律 **C-14** 的全库扫描（M1 的验收项之一）。

规矩：任何地方写厂商自评的 **61.7**，必须同时标注
「**partial score · 厂商自评 · 官方榜无该模型条目**」，
且不与 binary 口径的成绩并列；对外背书只用上一代 M3 的官方榜成绩。

判据（可自动判定）：

* **口径三件套**在出现的上下文（同一行 ±3 行）里齐了 → 达标；
* 或者那一行本身在**讲这条规矩**（含「口径 / 标注 / 必带」）→ 达标
  （例如变更记录、检查项定义、映射表里的「61.7 口径」）；
* 其余 → 不达标。

**底稿目录**（``docs/research/bottom-drafts/``）豁免：它是**证据记录**，
口径在同一章的专章里统一交代，逐行加标注会改写证据本身。
"""

from __future__ import annotations

import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SELF = Path(__file__).name
_EXEMPT_DIRS = ("docs/research/bottom-drafts",)
_SKIP_DIRS = {".git", ".venv", "__pycache__", "runs", ".scratch", ".pytest_cache", ".ruff_cache"}
_SUFFIXES = (".md", ".py")
_NEEDLE = "61.7"
_CALIBER_MARKERS = ("partial", "部分分")
_SELF_REPORT_MARKERS = ("自评",)
_LEADERBOARD_MARKERS = ("官方榜", "无条目", "无榜", "无独立条目")
_RULE_MARKERS = ("口径", "标注", "必带")


def _text_files() -> list[Path]:
    files: list[Path] = []
    for path in _REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in _SUFFIXES:
            continue
        if path.name == _SELF:
            continue
        relative = path.relative_to(_REPO_ROOT)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if any(relative.as_posix().startswith(prefix) for prefix in _EXEMPT_DIRS):
            continue
        files.append(path)
    return sorted(files)


class CitationDisciplineTest(unittest.TestCase):
    """61.7 的每一处都要带口径。"""

    def test_repo_contains_the_number_at_all(self):
        """守卫：扫描器本身没坏（仓库里确实还有 61.7 的引用）。"""
        hits = [
            path
            for path in _text_files()
            if _NEEDLE in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertTrue(hits, "全库扫不到 61.7 了，扫描规则可能失效")

    def test_every_mention_carries_the_caliber(self):
        problems: list[str] = []
        for path in _text_files():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for index, line in enumerate(lines):
                if _NEEDLE not in line:
                    continue
                low = max(0, index - 3)
                high = min(len(lines), index + 4)
                context = "\n".join(lines[low:high])
                caliber = (
                    any(marker in context for marker in _CALIBER_MARKERS)
                    and any(marker in context for marker in _SELF_REPORT_MARKERS)
                    and any(marker in context for marker in _LEADERBOARD_MARKERS)
                )
                mentions_the_rule = any(marker in line for marker in _RULE_MARKERS)
                if not (caliber or mentions_the_rule):
                    problems.append(
                        f"{path.relative_to(_REPO_ROOT)}:{index + 1}: {line.strip()[:120]}"
                    )
        self.assertEqual(problems, [], "这些地方写 61.7 没带口径标注：\n" + "\n".join(problems))

    def test_61_7_is_not_juxtaposed_with_binary_scores(self):
        """61.7 与 binary 口径的成绩并列时，同一行必须标 partial。"""
        problems: list[str] = []
        for path in _text_files():
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                lowered = line.lower()
                if _NEEDLE not in line or "binary" not in lowered:
                    continue
                if not any(marker in lowered for marker in ("partial", "部分分")):
                    problems.append(f"{path.relative_to(_REPO_ROOT)}:{index}: {line.strip()[:120]}")
        self.assertEqual(problems, [], "61.7 与 binary 口径并列且没标 partial：\n" + "\n".join(problems))

    def test_outward_endorsement_uses_the_official_m3_score_only(self):
        """对外背书只用上一代 M3 的官方榜成绩（75.19%）。"""
        problems: list[str] = []
        for path in _text_files():
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "75.19" not in line:
                    continue
                if "官方榜" not in line and "MiniMax M3" not in line and "M3 " not in line:
                    problems.append(f"{path.relative_to(_REPO_ROOT)}:{index}: {line.strip()[:120]}")
        self.assertEqual(problems, [], "75.19 没标官方榜出处：\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
