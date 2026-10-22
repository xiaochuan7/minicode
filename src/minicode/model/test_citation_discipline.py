"""引用纪律的**全库扫描**（C-14 是 M1 的验收项之一，C-13 是 M10 的验收项之一）。

**C-14**：任何地方写厂商自评的 **61.7**，必须同时标注
「**partial score · 厂商自评 · 官方榜无该模型条目**」，
且不与 binary 口径的成绩并列；对外背书只用上一代 M3 的官方榜成绩。

判据（可自动判定）：

* **口径三件套**在出现的上下文（同一行 ±3 行）里齐了 → 达标；
* 或者那一行本身在**讲这条规矩**（含「口径 / 标注 / 必带」）→ 达标
  （例如变更记录、检查项定义、映射表里的「61.7 口径」）；
* 其余 → 不达标。

**C-13**：harness 增益只许引用官方仓库 README 正文里的两组数
（换 harness **+19.6 / −53%**、代码执行 vs 同等 GUI+CLI **+6.1 / −14.8%**）。
另有一组更宽的区间（**+15.5~+33.6 分 / 步数 −55~63%**）**不在 README 正文**，
属禁止引用的一档：全库检索该区间，**除「禁用/未核」说明句外**，
每一处都必须带论文消融表的**表号**（arXiv 2610.22200）。

**底稿目录**（``docs/research/bottom-drafts/``）豁免：它是**证据记录**，
口径在同一章的专章里统一交代，逐行加标注会改写证据本身。
"""

from __future__ import annotations

import re
import subprocess
import unittest
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SELF = Path(__file__).name
_EXEMPT_DIRS = ("docs/research/bottom-drafts",)
#: 静态兜底：即使 git 不可用也必须跳过的目录。
_SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "runs",
    ".scratch",
    ".pytest_cache",
    ".ruff_cache",
    ".worktrees",
    ".tmp_runs",
}
_SUFFIXES = (".md", ".py")
_NEEDLE = "61.7"
_CALIBER_MARKERS = ("partial", "部分分")
_SELF_REPORT_MARKERS = ("自评",)
_LEADERBOARD_MARKERS = ("官方榜", "无条目", "无榜", "无独立条目")
_RULE_MARKERS = ("口径", "标注", "必带")

# -- C-13：被禁引用的那组区间 ------------------------------------------------

#: 区间特征串（两种写法都算命中：波浪号与连字符）。
_RANGE_NEEDLES = ("15.5", "33.6", "55~63", "55-63", "55–63")
#: **区间特征串的精确匹配**（正则）。
#:
#: ⚠️ **为什么要在串之外再收紧一层**：那组区间的单位是「**分**」（分数增益），
#: 而 **Wilson 区间的半宽**写成「**pp**」（百分点）—— 两者是**不同的东西**。
#: 例：``| Creative 组 n / 半宽 | 36 / 15.5pp |`` 里的 ``15.5`` 是
#: **半宽 15.5 个百分点**，**不是**「+15.5 分的 harness 增益」→
#: 它命中裸串是**误报**（实测：`docs/research/r-s7-eval-full.md` 两行）。
#:
#: ⇒ 判据：``15.5`` / ``33.6`` 后面**不接** ``pp`` 才算命中；
#: ``55~63`` / ``55-63`` / ``55–63`` 那一段（步数的变化率）原样命中。
_RANGE_NEEDLE_RE = re.compile(r"(?:15\.5|33\.6)(?!\s*pp)|55[~–-]63")
#: 「禁用/未核」说明句的标志 —— 出现在区间所在行即达标。
_PROHIBITION_MARKERS = (
    "不在",
    "禁止",
    "禁用",
    "未核",
    "0 命中",
    "必须先回查",
    "说明句",
    "引用纪律",
    "无法判定",
    "不许引",
)
#: 真要引用这组区间时必须带的出处：论文编号 + 消融表号。
_PAPER_ID = "2610.22200"
_TABLE_NUMBER_RE = re.compile(r"(表|Table)\s*\d")
#: **别的系统**的公开数字 —— **不在 C-14 的管辖范围**里。
#:
#: C-14 管的是**本仓厂商自评**的那一个数（OSWorld 2.0 的 partial score 61.7）。
#: 下面这几类是**别的系统**的公开数字，**不是**那个数：
#:
#: * ``视图`` / ``MVP`` —— MVP 的**多视图消融**（2/4/10 视图 → 61.0/**61.7**/60.6），
#:   是 ScreenSpot-Pro 的 grounding 准确率【一手·公开 arXiv 2512.08529】；
#: * ``GTA1`` —— 另一个模型（GTA1-7B）在 ScreenSpot-Pro 上的**官方成绩**。
#:
#: ⚠️ **判据放在**上下文**（±3 行）**里 —— 只在这几处上下文里跳过，
#: 其余任何地方的 61.7 仍然要带口径三件套（**规则本身没放松**）。
_EXTERNAL_NUMBER_MARKERS = ("视图", "MVP", "GTA1")
#: 「带增益的写法」—— ``+19.6 分`` / ``−53%`` 这类。
_GAIN_RE = re.compile(r"[+＋]\s?\d+(?:\.\d+)?\s*(?:分|points?)|−\s?\d+(?:\.\d+)?\s*%")
#: README 正文可核的两组数里出现的那几个数字。
_README_NUMBERS = ("19.6", "6.1", "53", "14.8")
#: 「在讲换 harness 的增益」的上下文标志（讲别的项目的数字不算）。
_HARNESS_GAIN_CONTEXT = ("OpenGCUA", "换 harness", "同 harness", "只换")


def _git_ignored(candidates: list[Path]) -> set[Path]:
    """问 git：哪些路径被 ``.gitignore`` 忽略了（一次批量调用）。

    「被 gitignore 的目录」**必须**跳过 —— 否则并行开发的工作树
    （``.worktrees/``）里的文档副本会被当成第二份正文扫进来，
    同一处引用被数了 N 次，全量测试假红。

    git 不可用时返回空集：静态 ``_SKIP_DIRS`` 仍然兜底。
    """
    if not candidates:
        return set()
    payload = "\0".join(path.as_posix() for path in candidates) + "\0"
    try:
        proc = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "check-ignore", "--stdin", "-z"],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode not in (0, 1):  # 0 = 有命中；1 = 一条都没命中
        return set()
    return {Path(item) for item in proc.stdout.split("\0") if item}


@lru_cache(maxsize=1)
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
    ignored = _git_ignored([path.relative_to(_REPO_ROOT) for path in files])
    return sorted(path for path in files if path.relative_to(_REPO_ROOT) not in ignored)


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

    def test_ignored_directories_are_not_scanned(self):
        """被 ``.gitignore`` 的目录（``.worktrees/`` 等）不许被扫进来。

        回归守卫：并行开发的每个工作树里都有一份文档副本，扫进去会把
        同一处引用数成 N 份 → 全量测试假红（2026-10-21 实测踩过）。
        """
        for ignored in ("runs/x.md", ".worktrees/M23/docs/x.md", ".scratch/x.md"):
            with self.subTest(ignored=ignored):
                self.assertIn(
                    Path(ignored),
                    _git_ignored([Path(ignored)]),
                    f"{ignored} 应当被 git 忽略（.gitignore 里要有它）",
                )
        scanned = {path.relative_to(_REPO_ROOT).as_posix() for path in _text_files()}
        self.assertFalse(
            [name for name in scanned if name.startswith((".worktrees/", "runs/"))],
            "被忽略的目录被扫进来了",
        )

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
                #: ⚠️ **别的系统**的公开数字不在 C-14 的管辖范围里（见常量说明）。
                if any(marker in context for marker in _EXTERNAL_NUMBER_MARKERS):
                    continue
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


class HarnessGainCitationDisciplineTest(unittest.TestCase):
    """**C-13**：harness 增益的引用纪律（工单 M10 AC 2）。

    只许引用官方仓库 README 正文里的两组数；另有一组更宽的区间
    （**+15.5~+33.6 分 / 步数 −55~63%**）**不在 README 正文**，
    属禁止引用的一档 —— 全库检索该区间：**除「禁用/未核」说明句外**，
    每一处都必须带论文消融表的**表号**。
    """

    def test_repo_contains_the_forbidden_range_at_all(self):
        """守卫：扫描器本身没坏（仓库里确实还有这组区间的引用）。"""
        hits = [
            path
            for path in _text_files()
            if any(needle in path.read_text(encoding="utf-8", errors="replace")
                   for needle in _RANGE_NEEDLES)
        ]
        self.assertTrue(hits, "全库扫不到那组区间了，扫描规则可能失效")

    def test_every_mention_is_a_prohibition_or_carries_a_table_number(self):
        problems: list[str] = []
        for path in _text_files():
            for index, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if not _RANGE_NEEDLE_RE.search(line):
                    continue
                prohibition = any(marker in line for marker in _PROHIBITION_MARKERS)
                cited_with_table = (
                    _PAPER_ID in line and _TABLE_NUMBER_RE.search(line) is not None
                )
                if not (prohibition or cited_with_table):
                    problems.append(
                        f"{path.relative_to(_REPO_ROOT)}:{index}: {line.strip()[:120]}"
                    )
        self.assertEqual(
            problems,
            [],
            "这组区间只能出现在「禁用/未核」说明句里，或带 arXiv 2610.22200 的消融表号：\n"
            + "\n".join(problems),
        )

    def test_readme_numbers_are_the_only_cited_ones(self):
        """讲 harness 增益的行必须落在 README 可核的两组数上。

        README 正文只给两组：**换 harness +19.6 / 步数 −53%**、
        **代码执行 vs 同等 GUI+CLI +6.1 分 / 步数 −14.8%**。
        本检查只管**OpenGCUA 的 harness 增益**（讲的是别的项目的数字不管）。
        """
        problems: list[str] = []
        for path in _text_files():
            for index, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if "harness" not in line.lower():
                    continue
                if any(needle in line for needle in _RANGE_NEEDLES):
                    continue  # 上一条判据管这一档
                if not any(marker in line for marker in _HARNESS_GAIN_CONTEXT):
                    continue  # 不是在讲换 harness 的增益
                if not _GAIN_RE.search(line):
                    continue
                if any(number in line for number in _README_NUMBERS):
                    continue
                problems.append(f"{path.relative_to(_REPO_ROOT)}:{index}: {line.strip()[:120]}")
        self.assertEqual(
            problems,
            [],
            "讲 harness 增益的行必须落在 README 可核的两组数上：\n" + "\n".join(problems),
        )


if __name__ == "__main__":
    unittest.main()
