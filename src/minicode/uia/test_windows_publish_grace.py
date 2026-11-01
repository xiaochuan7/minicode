"""``_publish`` 的一代宽限（W7 P2 #7 的红/绿测试）。

事故链：定位（发布 gen N）→ **校验层再查一次树**（发布 gen N+1，旧实现
在这里把 gen N 的 COM 元素全部 ``release()``）→ Invoke 按元素 id 取 COM
元素 → 拿到的是**已释放**指针 → InvokePattern 静默降级成重定位。

宽限语义：发布 gen N+1 时 gen N **退位不释放**（``live_element`` 查两代）；
发布 gen N+2 时才释放 gen N——宽限恰好一代，不无界积压。
"""

from __future__ import annotations

import unittest

from minicode.uia import windows


class _FakeRect:
    def __init__(self, rect=(0, 0, 10, 10)):
        self.left, self.top, self.right, self.bottom = rect


class _FakeElement:
    """最小 COM 替身（无 ``_ptr`` → 读数走 fail-closed 兜底）。"""

    def __init__(self, name: str):
        self._name = name
        self.released = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def control_type_name(self) -> str:
        return "Button"

    @property
    def automation_id(self) -> str:
        return ""

    @property
    def class_name(self) -> str:
        return "Button"

    @property
    def bounding_rect(self) -> _FakeRect:
        return _FakeRect()

    @property
    def native_window_handle(self) -> int:
        return 0

    def release(self) -> None:
        self.released = True


class PublishGraceTest(unittest.TestCase):
    """一代宽限：查树不杀刚定位的元素；宽限有界（不无界积压 COM 引用）。"""

    def test_the_previous_generation_survives_one_publish(self):
        """红：发布新代后，上一次观测的元素必须仍可按 id 取到。"""
        port = windows.WindowsUiaQueryPort()
        gen1 = port._publish([_FakeElement("按钮甲")])
        id1 = gen1[0].element_id
        self.assertIsNotNone(port.live_element(id1))
        port._publish([_FakeElement("按钮乙")])  # 校验层又查了一次树
        survivor = port.live_element(id1)
        self.assertIsNotNone(
            survivor, "一代宽限：校验层查树不得释放刚定位的元素"
        )
        self.assertFalse(survivor.released)

    def test_the_grace_is_bounded_to_one_generation(self):
        """gen N 在发布 gen N+2 时释放——宽限恰好一代。"""
        port = windows.WindowsUiaQueryPort()
        element1 = _FakeElement("甲")
        element2 = _FakeElement("乙")
        element3 = _FakeElement("丙")
        id1 = port._publish([element1])[0].element_id
        id2 = port._publish([element2])[0].element_id
        port._publish([element3])
        self.assertTrue(
            element1.released, "两代之前观测的元素必须已释放（不无界积压）"
        )
        self.assertIsNone(port.live_element(id1))
        survivor = port.live_element(id2)
        self.assertIsNotNone(survivor)
        self.assertFalse(survivor.released)

    def test_the_fresh_generation_stays_resolvable(self):
        """新代照常可取（宽限不改变现行为）。"""
        port = windows.WindowsUiaQueryPort()
        element1 = _FakeElement("甲")
        id1 = port._publish([element1])[0].element_id
        self.assertIs(port.live_element(id1), element1)


if __name__ == "__main__":
    unittest.main()
