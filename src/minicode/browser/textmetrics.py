"""**文本度量**这一层：可见文本的**实际区域**（工单 M28）。

浏览器通道的落点不再取**元素矩形中心**，改取**可见文本的实际包围盒中心** ——
扁平 DOM 上元素框是整行宽（``<div>提交订单</div>`` 铺满一行），
元素框中心离文字很远，落到那里不算「落点」。

这一层是**浏览器通道独有的**：

* 桌面通道（UIA）的落点来自**控件框**，**不引入**文本度量；
* 这里的读数是**页面自己给的**（渲染后的行盒），不是推算的。

⚠️ **判不了 ≠ 落点落矩形中心**：元素没有可见文本 / 文本被裁 / 字体未加载
→ :class:`~minicode.browser.elements.TextMeasurement` 的 ``rect`` 是 ``None``，
上层据此**判不了**并记下原因，**不静默回落到元素矩形中心**。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from minicode.action.geometry import is_valid_rect

from .elements import Rect, TextMeasurement
from .geometry import PageViewport, css_rect_to_physical

__all__ = [
    "TEXT_MEASURE_JS",
    "TEXT_STATUS_REASONS",
    "DEFAULT_UNMEASURED_REASON",
    "css_text_rect",
    "measure_visible_text",
]

#: 读不出可见文本时的**默认**原因（状态不认识时用它）。
DEFAULT_UNMEASURED_REASON = "读不出可见文本的实际区域"

#: 状态 → 人读的**原因**（判不了时记进失败原因里）。
TEXT_STATUS_REASONS: Mapping[str, str] = {
    "no-text": "元素没有可见文本",
    "clipped": "可见文本被裁掉了（可见部分为空）",
    "font-loading": "字体还没加载完（文本度量不可信）",
    "hidden": "元素不可见（display:none / visibility:hidden）",
    "no-element": "不是元素（读不到盒子）",
}

#: 页面里量「盒子 + 可见文本区域」的脚本（**一次** CDP 往返拿两样）。
#:
#: * 文本区域 = 所有**渲染出来的文本节点**的行盒的并集（``Range.getClientRects``）——
#:   这是「文字实际在哪儿」，不是「元素框在哪儿」；
#: * 再与**各级会裁剪的盒子**求交（元素自己 + 祖先里 ``overflow`` 不是 ``visible`` 的）——
#:   被裁掉的文字不算「可见文本」；
#: * 元素不可见 / 字体还没加载完 → 如实报状态，**不猜**。
TEXT_MEASURE_JS = """
function(){
  const node = this;
  const doc = node.ownerDocument || node;
  const win = doc.defaultView || window;
  const isText = node.nodeType === 3;
  const el = isText ? node.parentElement : node;
  const fail = function(status){ return {status: status, box: null, text: null}; };
  if (!el || el.nodeType !== 1 || !el.getBoundingClientRect) {
    // 不是元素（文档 / 注释……）→ **没有盒子**，上层按「读不到坐标」丢掉它。
    return fail('no-element');
  }
  const own = el.getBoundingClientRect();
  const box = {x: own.x, y: own.y, w: own.width, h: own.height};
  // ⚠️ **盒子与文本区域分开**：量不到文本时**盒子照样给**
  // （元素还在快照里，只是「可见文本的实际区域」判不了）——
  // 否则元素会被整个丢掉，报成「目标不存在」（覆盖失败），那是另一回事。
  const noText = function(status){ return {status: status, box: box, text: null}; };
  const style = win.getComputedStyle(el);
  if (!style || style.display === 'none' || style.visibility === 'hidden') {
    return noText('hidden');
  }
  // 字体没加载完 → 文字的位置可能是回退字体的，度量不可信。
  if (doc.fonts && doc.fonts.size > 0 && doc.fonts.status !== 'loaded') {
    return noText('font-loading');
  }
  const rects = [];
  const collect = function(textNode){
    if (!textNode.nodeValue || !textNode.nodeValue.trim()) return;
    const parent = textNode.parentElement;
    if (parent) {
      const ps = win.getComputedStyle(parent);
      if (!ps || ps.display === 'none' || ps.visibility === 'hidden') return;
    }
    const range = doc.createRange();
    range.selectNodeContents(textNode);
    const list = range.getClientRects();
    for (let i = 0; i < list.length; i++) {
      const r = list[i];
      if (r.width > 0 && r.height > 0) rects.push(r);
    }
  };
  if (isText) {
    collect(node);
  } else {
    const walker = doc.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    let t = walker.nextNode();
    while (t) { collect(t); t = walker.nextNode(); }
  }
  if (!rects.length) return noText('no-text');
  let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
  for (const r of rects) {
    if (r.left < left) left = r.left;
    if (r.top < top) top = r.top;
    if (r.right > right) right = r.right;
    if (r.bottom > bottom) bottom = r.bottom;
  }
  // 可见区域 = 文本区域 ∩ 各级「会裁剪」的盒子。
  let clipLeft = -Infinity, clipTop = -Infinity;
  let clipRight = Infinity, clipBottom = Infinity;
  for (let p = el; p; p = p.parentElement) {
    const ps = win.getComputedStyle(p);
    if (!ps) break;
    if (ps.overflow === 'visible' && ps.overflowX === 'visible'
        && ps.overflowY === 'visible') continue;
    const r = p.getBoundingClientRect();
    if (r.left > clipLeft) clipLeft = r.left;
    if (r.top > clipTop) clipTop = r.top;
    if (r.right < clipRight) clipRight = r.right;
    if (r.bottom < clipBottom) clipBottom = r.bottom;
  }
  const vLeft = Math.max(left, clipLeft);
  const vTop = Math.max(top, clipTop);
  const vRight = Math.min(right, clipRight);
  const vBottom = Math.min(bottom, clipBottom);
  if (vRight - vLeft <= 0 || vBottom - vTop <= 0) return noText('clipped');
  return {
    status: 'ok',
    box: box,
    text: {x: vLeft, y: vTop, w: vRight - vLeft, h: vBottom - vTop}
  };
}
"""


def css_text_rect(payload: Mapping[str, Any]) -> Rect | None:
    """几何读数里的**可见文本区域**（**CSS 像素**的矩形；读不到 = ``None``）。"""
    text = payload.get("text")
    if not isinstance(text, Mapping):
        return None
    try:
        return (
            float(text["x"]),
            float(text["y"]),
            float(text["w"]),
            float(text["h"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def measure_visible_text(
    payload: Mapping[str, Any], viewport: PageViewport
) -> TextMeasurement:
    """页面给的**几何读数** → 可见文本的实际区域（**物理表面空间**）。

    * ``payload`` —— 页面脚本的返回值（``status`` + ``box`` + ``text``，
      ``text`` 是 **CSS 像素**）；
    * ``viewport`` —— 视口在物理表面空间的位置与比例。

    ⚠️ **换算只有一处**（:func:`minicode.browser.geometry.css_rect_to_physical`）：
    页面里的坐标是 CSS 像素，本项目的唯一真值空间是物理表面空间。
    """
    status = str(payload.get("status") or "")
    css_rect = css_text_rect(payload)
    if status != "ok" or css_rect is None:
        return TextMeasurement(
            rect=None,
            reason=TEXT_STATUS_REASONS.get(status, DEFAULT_UNMEASURED_REASON),
        )
    rect = css_rect_to_physical(css_rect, viewport)
    if not is_valid_rect(rect):
        return TextMeasurement(rect=None, reason="可见文本的区域读不成合法矩形")
    return TextMeasurement(rect=rect, reason="")
