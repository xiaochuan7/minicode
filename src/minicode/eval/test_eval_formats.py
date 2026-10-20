"""**评测脚本坐标口径**的外部行为单测（工单 M7 AC 5 / AC 6 / AC 7 / AC 8）。

钉死四种边界转换（每种一个具体数值例子 + 一个反例），并做「接了官方脚本
但分数全 0」那类假象的**防假象自检**：把 GT bbox 中心当预测点喂给自己的
wrapper，目标脚本给出的命中率必须接近 100%，不是 0。

口径来源（本地已 clone 的四个仓库，逐行读过）：

* ScreenSpot-Pro 官方 ``eval_screenspot_pro.py::eval_sample_positive_gt``
* ZoomClick 副本 ``grounding/eval_sspro_zoomclick.py::eval_sample_positive_gt``
* CCF 仓 ScreenSpot-v2 ``src/eval.py``（GT bbox 是 ``[x, y, W, H]``）
* ShowUI ``main/eval_screenspot.py::get_bbox``（网格值：**实际脚本是 1000**）

⚠️ **两套命名分属不同域**：本模块的常量是**评测目标脚本的输出格式**，
不是模型量纲分母（那只能来自运行时标定）。本模块因此**不 import
:mod:`minicode.coord`**。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from minicode.eval.formats import (
    CCF_SCREENSPOT_V2,
    SCREENSPOT_PRO,
    SHOWUI,
    ZOOMCLICK,
    EvalBBoxFormat,
    EvalPointFormat,
    EvalSample,
    EvalTarget,
    bbox_xywh_to_xyxy,
    bbox_xyxy_to_xywh,
    point_in_bbox_xywh,
    point_in_bbox_xyxy,
    self_check_hit_rate,
    to_grid_1000,
    to_normalized,
    to_original_pixels,
)

_EVAL_DIR = Path(__file__).resolve().parent

#: 本机已 clone 的四个参考仓库（**不在仓库里**，没有就跳过那一类核对）。
_LOCAL_REPOS = Path(r"C:\Users\22197\Desktop\DSH-work\repos")


class ConversionTests(unittest.TestCase):
    """AC 5 · 四种边界转换，各带一个具体数值例子。"""

    def test_original_pixels_to_normalized(self):
        """原图像素 → 归一化 [0,1]（ScreenSpot-Pro 官方脚本的 point 期望）。"""
        self.assertEqual(to_normalized((1280, 800), (2560, 1600)), (0.5, 0.5))
        self.assertEqual(to_normalized((0, 0), (2560, 1600)), (0.0, 0.0))
        self.assertEqual(to_normalized((2560, 1600), (2560, 1600)), (1.0, 1.0))

    def test_original_pixels_to_original_pixels(self):
        """原图像素 → 原图像素（ZoomClick 副本的 point 期望；整数像素）。"""
        self.assertEqual(to_original_pixels((1280, 800), (2560, 1600)), (1280, 800))
        self.assertEqual(to_original_pixels((1280.4, 799.6), (2560, 1600)), (1280, 800))

    def test_bbox_xyxy_to_xywh(self):
        """GT bbox [x1,y1,x2,y2] → [x,y,W,H] 宽高形式（CCF 仓 ScreenSpot-v2 脚本）。"""
        self.assertEqual(bbox_xyxy_to_xywh((100, 200, 300, 400)), (100, 200, 200, 200))
        self.assertEqual(bbox_xywh_to_xyxy((100, 200, 200, 200)), (100, 200, 300, 400))

    def test_original_pixels_to_grid(self):
        """原图像素 → 网格（ShowUI 脚本的 point 期望）。

        ⚠️ **实际脚本是 1000，不是 999**（``main/eval_screenspot.py`` 的
        ``get_bbox`` 里写的是 ``int(item * 1000)``）。
        """
        self.assertEqual(to_grid_1000((1280, 800), (2560, 1600)), (500, 500))
        self.assertEqual(to_grid_1000((2560, 1600), (2560, 1600)), (1000, 1000))

    def test_each_target_wraps_the_same_original_pixel_point(self):
        """同一个原图像素点，四个目标脚本各要各的形状。"""
        point = (1280, 800)
        size = (2560, 1600)
        self.assertEqual(SCREENSPOT_PRO.wrap(point, size), (0.5, 0.5))
        self.assertEqual(ZOOMCLICK.wrap(point, size), (1280, 800))
        self.assertEqual(CCF_SCREENSPOT_V2.wrap(point, size), (1280, 800))
        self.assertEqual(SHOWUI.wrap(point, size), (500, 500))


class CounterExampleTests(unittest.TestCase):
    """AC 5 · 每种转换各带一个反例（混用会得出相反结论）。"""

    def test_normalized_point_fed_to_a_pixel_script_misses(self):
        """反例①：把归一化点当像素喂给按像素判定的脚本 → 永远判错。"""
        sample = EvalSample(bbox=(1000, 600, 1400, 1000), image_size=(2560, 1600))
        normalized = to_normalized((1200, 800), sample.image_size)
        # ZoomClick 副本按**像素**判定：归一化点 (0.46875, 0.5) 落在 bbox 左边 → 判错。
        self.assertFalse(ZOOMCLICK.hit(normalized, sample))
        # 按像素判定：同一个点命中。
        self.assertTrue(ZOOMCLICK.hit((1200, 800), sample))

    def test_pixel_point_fed_to_a_normalized_script_misses(self):
        """反例②：把像素点当归一化喂给 SSPro 官方脚本 → 永远判错（分数全 0 的假象）。"""
        sample = EvalSample(bbox=(1000, 600, 1400, 1000), image_size=(2560, 1600))
        self.assertFalse(SCREENSPOT_PRO.hit((1200, 800), sample))
        self.assertTrue(SCREENSPOT_PRO.hit(to_normalized((1200, 800), sample.image_size), sample))

    def test_bbox_xywh_read_as_xyxy_flips_the_verdict(self):
        """反例③：把 [x,y,W,H] 当 [x1,y1,x2,y2] 判定 → **相反结论**。"""
        sample = EvalSample(bbox=(100, 200, 300, 400), image_size=(2560, 1600))
        point = (250, 300)
        # 正确口径（宽高形式）：x ≤ px ≤ x + W。
        self.assertTrue(CCF_SCREENSPOT_V2.hit(point, sample))
        # 同一个 GT 按 xyxy 读：[100,200,200,200] → 250 > 200 → 判错。
        self.assertTrue(point_in_bbox_xywh(point, bbox_xyxy_to_xywh((100, 200, 300, 400))))
        self.assertFalse(point_in_bbox_xyxy(point, bbox_xyxy_to_xywh((100, 200, 300, 400))))

    def test_grid_999_instead_of_1000_flips_the_right_edge_verdict(self):
        """反例④：网格值落错一档（999 而非实际脚本的 1000）→ 边缘样本判错。

        ⚠️ **不报错**，只是分数悄悄低一点 —— 这正是要钉死网格值的理由。
        """
        edge = EvalSample(bbox=(2558, 0, 2560, 10), image_size=(2560, 1600))
        # 实际脚本（1000 网格）：同一个点命中。
        self.assertTrue(SHOWUI.hit(to_grid_1000((2559, 5), edge.image_size), edge))
        # 错用 999 网格：同一个点落在 GT 左边界之外 → 判错（不报错）。
        wrong_grid = (int(2559 / 2560 * 999), int(5 / 1600 * 999))
        self.assertFalse(SHOWUI.hit(wrong_grid, edge))


class SelfCheckTests(unittest.TestCase):
    """AC 6 · 防假象自检：GT 中心当预测点 → 命中率必须接近 100%，不是 0。"""

    def _samples(self):
        return [
            EvalSample(bbox=(1000, 600, 1400, 1000), image_size=(2560, 1600)),
            EvalSample(bbox=(10, 20, 30, 40), image_size=(2560, 1600)),
            EvalSample(bbox=(2550, 1590, 2560, 1600), image_size=(2560, 1600)),
        ]

    def test_self_check_hit_rate_is_one_for_every_target(self):
        for target in (SCREENSPOT_PRO, ZOOMCLICK, CCF_SCREENSPOT_V2, SHOWUI):
            with self.subTest(target=target.name):
                rate = self_check_hit_rate(self._samples(), target)
                self.assertEqual(rate, 1.0)
                self.assertNotEqual(rate, 0.0)

    def test_wrapped_gt_center_hits_the_official_style_check(self):
        """走一遍「wrapper → 目标脚本的判定」：GT 中心必须命中。"""
        sample = EvalSample(bbox=(1000, 600, 1400, 1000), image_size=(2560, 1600))
        center = ((1000 + 1400) // 2, (600 + 1000) // 2)
        prediction = SCREENSPOT_PRO.wrap(center, sample.image_size)
        self.assertTrue(SCREENSPOT_PRO.hit(prediction, sample))

    def test_a_wrong_wrapper_scores_zero_instead_of_one(self):
        """反例：wrapper 若不转坐标（直接回原图像素），官方脚本给 0 分。"""
        sample = EvalSample(bbox=(1000, 600, 1400, 1000), image_size=(2560, 1600))
        center = ((1000 + 1400) // 2, (600 + 1000) // 2)
        self.assertFalse(SCREENSPOT_PRO.hit(center, sample))


class OfficialScriptCrossCheckTests(unittest.TestCase):
    """AC 6 的强形式：**直接跑官方脚本自己的判定函数**，核对本仓的复刻。

    官方脚本都 `import torch`（本机没装），但它们的**判定函数是纯的** ——
    这里把那段源码从本地 clone 里**取出来直接执行**（不是照抄一份），
    于是「本仓的复刻与官方脚本有偏差」这件事会被当场抓住。

    本机没有那四个 clone 时**跳过**（不伪造结论）。
    """

    def _official_function(self, relative_path: str, function_name: str):
        """从本地 clone 里取出官方函数的源码并执行它。"""
        path = _LOCAL_REPOS / relative_path
        if not path.exists():
            self.skipTest(f"本地没有 {relative_path}（官方脚本核对跳过）")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                namespace: dict = {}
                exec(compile(ast.Module([node], []), str(path), "exec"), namespace)  # noqa: S102
                return namespace[function_name]
        self.skipTest(f"{relative_path} 里没有 {function_name}（官方脚本核对跳过）")

    def _samples(self):
        return [
            EvalSample(bbox=(1000, 600, 1400, 1000), image_size=(2560, 1600)),
            EvalSample(bbox=(10, 20, 30, 40), image_size=(2560, 1600)),
            EvalSample(bbox=(2550, 1590, 2560, 1600), image_size=(2560, 1600)),
        ]

    def test_screenspot_pro_official_judgement_agrees_with_our_port(self):
        """ScreenSpot-Pro 官方 ``eval_sample_positive_gt``：本仓复刻与它一致。"""
        official = self._official_function(
            "sspro/eval_screenspot_pro.py", "eval_sample_positive_gt"
        )
        for sample in self._samples():
            x1, y1, x2, y2 = sample.bbox
            center = ((x1 + x2) // 2, (y1 + y2) // 2)
            prediction = SCREENSPOT_PRO.wrap(center, sample.image_size)
            official_verdict = official(
                {"bbox": sample.bbox, "img_size": list(sample.image_size)},
                {"point": list(prediction)},
            )
            self.assertEqual(official_verdict, "correct")
            self.assertTrue(SCREENSPOT_PRO.hit(prediction, sample))

    def test_zoomclick_official_judgement_agrees_with_our_port(self):
        """ZoomClick 副本 ``eval_sample_positive_gt``：本仓复刻与它一致。"""
        official = self._official_function(
            "zoomclick/grounding/eval_sspro_zoomclick.py", "eval_sample_positive_gt"
        )
        for sample in self._samples():
            x1, y1, x2, y2 = sample.bbox
            center = ((x1 + x2) // 2, (y1 + y2) // 2)
            prediction = ZOOMCLICK.wrap(center, sample.image_size)
            official_verdict = official(
                {"bbox": sample.bbox}, {"point": list(prediction)}
            )
            self.assertEqual(official_verdict, "correct")
            self.assertTrue(ZOOMCLICK.hit(prediction, sample))

    def test_showui_official_helpers_agree_with_our_port(self):
        """ShowUI 官方 ``get_bbox`` + ``pointinbbox``：本仓复刻与它一致。"""
        get_bbox = self._official_function("showui/main/eval_screenspot.py", "get_bbox")
        point_in_bbox = self._official_function(
            "showui/main/eval_screenspot.py", "pointinbbox"
        )
        for sample in self._samples():
            x1, y1, x2, y2 = sample.bbox
            center = ((x1 + x2) // 2, (y1 + y2) // 2)
            prediction = SHOWUI.wrap(center, sample.image_size)
            # 官方：GT 是 [x, y, W, H] → 归一化 → int(*1000)。
            official_gt = get_bbox(
                list(bbox_xyxy_to_xywh(sample.bbox)), list(sample.image_size), True
            )
            self.assertTrue(point_in_bbox(list(prediction), official_gt))
            self.assertTrue(SHOWUI.hit(prediction, sample))


class NamingDisciplineTests(unittest.TestCase):
    """AC 7 · 两套命名分属不同域：输出格式常量 ≠ 模型量纲分母。"""

    def test_eval_package_does_not_import_the_model_scale_source(self):
        import re

        offenders: list[str] = []
        for path in sorted(_EVAL_DIR.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"^\s*(from|import)\s+minicode\.coord\b", text, re.MULTILINE):
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_format_names_do_not_claim_to_be_a_scale(self):
        names = [member.value for member in EvalPointFormat] + [
            member.value for member in EvalBBoxFormat
        ]
        for name in names:
            self.assertNotIn("scale", name)
            self.assertNotIn("denominator", name)

    def test_showui_grid_value_is_the_one_in_the_actual_script(self):
        """AC 8 · 钉死：ShowUI 的网格值以**实际脚本**为准 = 1000（不是 999）。"""
        self.assertIs(SHOWUI.point_format, EvalPointFormat.GRID_1000)
        self.assertEqual(SCREENSPOT_PRO.point_format, EvalPointFormat.NORMALIZED_01)
        self.assertEqual(ZOOMCLICK.point_format, EvalPointFormat.ORIGINAL_PIXELS)
        self.assertEqual(CCF_SCREENSPOT_V2.bbox_format, EvalBBoxFormat.XYWH)
        self.assertEqual(SCREENSPOT_PRO.bbox_format, EvalBBoxFormat.XYXY)

    def test_every_target_records_its_source_script(self):
        for target in (SCREENSPOT_PRO, ZOOMCLICK, CCF_SCREENSPOT_V2, SHOWUI):
            self.assertTrue(target.source)
            self.assertIn(".py", target.source)


class TargetDefinitionTests(unittest.TestCase):
    """四个目标脚本的口径形状。"""

    def test_targets_are_distinct(self):
        targets = [SCREENSPOT_PRO, ZOOMCLICK, CCF_SCREENSPOT_V2, SHOWUI]
        self.assertEqual(len({target.name for target in targets}), 4)

    def test_target_definitions_are_consistent_with_their_formats(self):
        for target in (
            SCREENSPOT_PRO,
            ZOOMCLICK,
            CCF_SCREENSPOT_V2,
            SHOWUI,
        ):
            with self.subTest(target=target.name):
                self.assertIsInstance(target, EvalTarget)
                self.assertIn(target.point_format, tuple(EvalPointFormat))
                self.assertIn(target.bbox_format, tuple(EvalBBoxFormat))


if __name__ == "__main__":
    unittest.main()
