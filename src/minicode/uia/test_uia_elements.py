"""控件框读法与元素身份的单测（工单 M3 AC 3、AC 4 的一部分）。

* AC 3 —— **控件框按 4 个 int 读**：按 ``double`` 读会得到 1e-311 量级、
  四舍五入全 0 的形态，必须被识别成**无效坐标**（不许被当作合法左上角）。
* AC 4 —— **全 0 候选被丢弃**：包围盒四个值全 0（或退化成零面积）不进候选集。
"""

from __future__ import annotations

import unittest

from minicode.uia.elements import (
    UiaElement,
    center_point,
    element_identity,
    is_same_place,
    is_valid_rect,
    read_rect,
    rename_elements,
)


class ReadRectTest(unittest.TestCase):
    """AC 3：控件框的读法。"""

    def test_a_normal_rect_is_read_as_four_ints(self):
        self.assertEqual(read_rect((10, 1528, 238, 1600)), (10, 1528, 238, 1600))

    def test_the_double_read_shape_is_an_invalid_coordinate(self):
        # 按 double 读 BoundingRectangle 会得到 1e-311 量级的非规格化数。
        raw = (1e-311, 1e-311, 1e-311, 1e-311)
        self.assertIsNone(read_rect(raw))

    def test_the_double_read_shape_is_not_taken_as_the_top_left_corner(self):
        raw = (1e-311, 1e-311, 1e-311, 1e-311)
        rect = read_rect(raw)
        self.assertNotEqual(rect, (0, 0, 0, 0))
        self.assertFalse(is_valid_rect((0, 0, 0, 0)))

    def test_an_all_zero_rect_is_invalid(self):
        self.assertIsNone(read_rect((0, 0, 0, 0)))

    def test_a_degenerate_zero_area_rect_is_invalid(self):
        self.assertIsNone(read_rect((10, 20, 10, 80)))
        self.assertIsNone(read_rect((10, 20, 90, 20)))

    def test_a_rect_at_the_origin_is_valid(self):
        self.assertEqual(read_rect((0, 0, 100, 30)), (0, 0, 100, 30))

    def test_negative_coordinates_are_valid(self):
        # 多屏：虚拟桌面原点可能是负的。
        self.assertEqual(read_rect((-100, -50, 10, 30)), (-100, -50, 10, 30))

    def test_a_non_finite_value_is_invalid(self):
        self.assertIsNone(read_rect((float("nan"), 0, 10, 10)))
        self.assertIsNone(read_rect((float("inf"), 0, 10, 10)))

    def test_a_wrong_arity_is_invalid(self):
        self.assertIsNone(read_rect((1, 2, 3)))


class ValidRectTest(unittest.TestCase):
    """AC 4：全 0 / 零面积的候选不合法。"""

    def test_all_zero_is_not_valid(self):
        self.assertFalse(is_valid_rect((0, 0, 0, 0)))

    def test_zero_area_is_not_valid(self):
        self.assertFalse(is_valid_rect((5, 5, 5, 5)))

    def test_a_real_rect_is_valid(self):
        self.assertTrue(is_valid_rect((5, 5, 25, 45)))


class CenterPointTest(unittest.TestCase):
    """AC 2：落点取控件框中心（物理像素）。"""

    def test_the_center_of_an_even_sized_rect(self):
        self.assertEqual(center_point((10, 20, 110, 60)), (60, 40))

    def test_the_center_of_an_odd_sized_rect_is_within_half_a_pixel(self):
        point = center_point((10, 20, 111, 61))
        self.assertLessEqual(abs(point[0] - 60.5), 0.5)
        self.assertLessEqual(abs(point[1] - 40.5), 0.5)


class SamePlaceTest(unittest.TestCase):
    """重定位时靠「同一个位置」把同名的两个控件分开。"""

    def test_two_rects_at_the_same_place_are_the_same_place(self):
        self.assertTrue(is_same_place((1000, 1520, 1080, 1600), (1000, 1520, 1080, 1608)))

    def test_two_rects_far_apart_are_not_the_same_place(self):
        self.assertFalse(is_same_place((1000, 1520, 1080, 1600), (2000, 1520, 2080, 1600)))

    def test_rects_that_only_touch_are_not_the_same_place(self):
        self.assertFalse(is_same_place((0, 0, 100, 100), (100, 0, 200, 100)))


class ElementIdentityTest(unittest.TestCase):
    """执行前重读树靠**身份**认出「还是那个控件」。"""

    def test_the_identity_ignores_the_element_id(self):
        left = UiaElement(name="开始", control_type="Button", class_name="", element_id="obs1#0")
        right = UiaElement(name="开始", control_type="Button", class_name="", element_id="obs2#7")
        self.assertEqual(element_identity(left), element_identity(right))

    def test_a_different_control_has_a_different_identity(self):
        left = UiaElement(name="开始", control_type="Button", class_name="X")
        right = UiaElement(name="搜索", control_type="Button", class_name="X")
        self.assertNotEqual(element_identity(left), element_identity(right))


class FreshElementIdTest(unittest.TestCase):
    """AC 7：元素 id 每次观测重新生成。"""

    def test_the_ids_are_renamed(self):
        elements = (UiaElement(name="开始"), UiaElement(name="搜索"))
        renamed = rename_elements(elements, "obs2")
        self.assertEqual([e.element_id for e in renamed], ["obs2#0", "obs2#1"])

    def test_the_previous_ids_do_not_resolve(self):
        elements = (UiaElement(name="开始", element_id="obs1#0"),)
        renamed = rename_elements(elements, "obs2")
        self.assertNotEqual(elements[0].element_id, renamed[0].element_id)


if __name__ == "__main__":
    unittest.main()
