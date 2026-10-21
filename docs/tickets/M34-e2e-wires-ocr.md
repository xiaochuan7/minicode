M34 | 端到端流程接 OCR：W-1② 与 WX-1③ 自动成立 | Blocked by: M11, M27 | Status: ready-for-agent

## Parent

- 本规格 Issue：https://github.com/xiaochuan7/minicode/issues/2
- 本单 Issue 号：<待回填>

## What to build

M27 给「文本比对」这一路接了**画面 OCR**（`src/minicode/verification/`），
并在真机上跑通了三处：Windows 设置的缩放值、DSH 界面的数字读数、微信的聊天区标题。

但**端到端执行器**（`src/minicode/e2e/real.py` / `src/minicode/e2e/apps.py`）
**还没有接 OCR** —— 它在 `src/minicode/e2e/`，不在 M27 的边界内。
于是 M14 的 **W-1②**（缩放值）与 M15 的 **WX-1③**（聊天区标题）
在**端到端流程里**仍然不自动成立：真机跑到了那一步，校验层只能从控件树里找，
而缩放值与聊天区标题**根本不在树里** → **判不了**。

本单把 OCR 接到端到端执行器上。

## Acceptance criteria

- [ ] **端到端执行器接上 OCR**：构造 `VerificationLayer` 时注入 OCR 端口
      （真机后端 `WindowsOcrPort`），并按步骤给出**要读的屏幕区域**（`ocr_region`）
      与**区域来源**（`ocr_region_source`，人读）。
- [ ] **W-1② 在端到端里成立**：Windows 设置显示页上的缩放值被 OCR 读出，
      与界面显示一致；读不出 → **判不了**（**不是**「不成立」）。
- [ ] **WX-1③ 在端到端里成立**：进入某个联系人的聊天后，OCR 读出聊天区标题。
- [ ] **每步记 `truth_source`**：端到端报告里能读出这一步的真值是**画面量的**
      还是**树里取的**。
- [ ] **口径不变**：OCR 仍然是**「文本比对」这一路的第二个来源**，**不是**第四种验证手段
      （`means` 的键仍然是 `tree` / `text` / `pixels` 三个）；OCR 不可用 → **判不了**。
- [ ] **只读**：OCR 只读画面，**不产生动作、不注入输入**。
- [ ] **OCR 不可用时端到端不崩**：后端不可用（没装语言包 / 读帧失败）→ 如实记「判不了」，
      端到端流程照常走完并如实报告，**不假装成立也不假装不成立**。

## Blocked by

- M11 — 校验层（三种验证手段的判据与「判不了」这一档）
- M27 — 文本比对接 OCR（端口、真值来源与「判不了」口径）
