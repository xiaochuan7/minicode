# 真实终端输出全记录

本页存放 README「用法」一节引用的完整终端输出。全部输出于 2026-10-26 在本机
（Windows 11，2560×1600 物理 pixels，DPI 144）用 `.scratch/release-venv` 环境实际采集，
采集命令写在每节标题下方，可自行复跑。README 正文只保留摘录，不做任何改写或美化。

## `minicode --help`

采集命令：

```powershell
.scratch/release-venv/Scripts/minicode.exe --help
```

```text
minicode —— Windows 桌面 computer-use agent

用法：minicode <命令> [参数]

命令：
  capability  UIA 通道能力探测：读控件树并回报候选
  calib       视觉通道标定：探针图 → 判定标度 → 准入判定
  ground      视觉定位：截图 → 候选表 → 模型选编号 → 落点
  action      最小闭环：截图 → 定位 → 点击 → 校验
  eval        评测：数据集跑批与统计（ScreenSpot-Pro 等）
  e2e         端到端任务跑批（真机；动真机前先拿桌面锁）
  serve       本地 HTTP 服务 / 动作记录查询
  mcp         MCP server：把动作能力暴露给外部 agent

各命令的完整参数：minicode <命令> --help
两条最常见的用法：
  minicode --help
  minicode action --target "开始"
```

## `minicode action --fake --out runs`（假桌面，不碰真鼠标）

采集命令：

```powershell
$env:PYTHONIOENCODING = "utf-8"
.scratch/release-venv/Scripts/minicode.exe action --fake --out runs
```

```text
========== M0 最小闭环 ==========
启动自检：awareness=PER_MONITOR_AWARE_V2 | physical=(2560, 1600) | device_caps_dpi=144 | capture=(2560, 1600)
注入自检：sizeof(INPUT)=40 (期望 40) | probe_return=1
动作结果：ok=True 落点=(1049, 1564)
意图    ：点击「开始」
回读偏差：0 px（验收要求 0）
落点复验：偏差=0.5 px（要求 ≤2 px）→ 通过
动作记录：runs\action-records.jsonl
{
  "protocol_version": "cua.action/1",
  "timestamp": "2026-10-26T13:40:57+08:00",
  "action": "click",
  "intent": "点击「开始」",
  "target": {
    "name": "开始",
    "rect": [
      1016,
      1528,
      1083,
      1600
    ],
    "control_type": "Button",
    "window_handle": 65784,
    "window_title": "任务栏"
  },
  "point": {
    "x": 1049,
    "y": 1564,
    "space": "physical-surface"
  },
  "capture": {
    "width": 2560,
    "height": 1600
  },
  "sent_size": {
    "width": 2560,
    "height": 1600
  },
  "image_source": "virtual-desktop|fake",
  "screenshot_sha256": "7d145e42fb8059ca095ed6eb032ebfefe0aede3cc86ce2c017a3defed2adaad6",
  "result": {
    "ok": true,
    "error_code": null,
    "error_message": null,
    "cursor_readback": [
      1049,
      1564
    ],
    "cursor_delta_px": 0
  },
  "rejected_candidates": [],
  "timings_ms": {
    "locate": 0.03840000135824084,
    "decision": 0.0,
    "execute_verify": 100.37930001271889
  },
  "timings_share": {
    "locate": 0.00038240271737808885,
    "decision": 0.0,
    "execute_verify": 0.9996175972826219
  },
  "action_timing": {
    "pre_move_ms": 60.26319999364205,
    "hold_ms": 40.06110000773333,
    "total_ms": 100.33499999553896,
    "double_click_gap_ms": null,
    "double_click_requested_gap_ms": null,
    "scroll_settle_ms": 0.0,
    "scroll_delta": 0,
    "wait_ms": 0
  },
  "verification": {
    "landing_delta_px": 0.5,
    "landing_verified": true
  },
  "query_scope": "",
  "params": {
    "x": 1049,
    "y": 1564,
    "space": "physical-surface"
  },
  "failures": {
    "locate": null,
    "decision": null,
    "execute": null
  },
  "redaction": null,
  "selection_echo": null,
  "allows_visual_fallback": false,
  "channel": "uia",
  "failure_type": "",
  "model_calls": 0,
  "unavailable_capture_sources": {},
  "candidate_limit": 0,
  "candidate_limit_source": "",
  "visual_admission": null,
  "coarse_point": null,
  "crop_plan": null,
  "grounding_echoes": [],
  "fallback": false,
  "fallback_reason": "",
  "fallback_cause": "",
  "gate_hit": false,
  "gate_reasons": [],
  "gate_undecidable": [],
  "refine_enabled": false,
  "state": {
    "before": null,
    "after": null
  },
  "verification_result": null,
  "approval": null,
  "privacy": null,
  "notes": []
}
==================================
```

## `minicode action --target "开始"`（真机最小闭环）

采集命令（动真机前先拿桌面锁，跑完释放）：

```powershell
$env:PYTHONIOENCODING = "utf-8"
.scratch/release-venv/Scripts/python.exe scripts/desktop_lock.py acquire --ticket readme
.scratch/release-venv/Scripts/minicode.exe action --target "开始"
.scratch/release-venv/Scripts/python.exe scripts/desktop_lock.py release
```

```text
========== 本机 DPI 状态报告 ==========
  DPI 感知模式      : PER_MONITOR_AWARE_V2  [OK]
  物理分辨率        : 2560 x 1600 px
  逻辑分辨率        : 1707 x 1067 px
  缩放比            : 1.5x   (有效 DPI 144)
  GetDeviceCaps     : HORZRES=2560 VERTRES=1600 LOGPIXELSX=144  [与 GetSystemMetrics 一致]
  虚拟桌面          : 原点=(0, 0)  尺寸=2560 x 1600 px
  显示器数量        : 1
  坐标约定          : 物理像素，原点=虚拟桌面左上角
=========================================
========== M0 最小闭环 ==========
启动自检：awareness=PER_MONITOR_AWARE_V2 | physical=(2560, 1600) | device_caps_dpi=144 | capture=(2560, 1600)
注入自检：sizeof(INPUT)=40 (期望 40) | probe_return=1
动作结果：ok=True 落点=(917, 1564)
意图    ：点击「开始」
回读偏差：0 px（验收要求 0）
落点复验：偏差=0.0 px（要求 ≤2 px）→ 通过
动作记录：runs\action-records.jsonl
{
  "protocol_version": "cua.action/1",
  "timestamp": "2026-10-26T13:34:57+08:00",
  "action": "click",
  "intent": "点击「开始」",
  "target": {
    "name": "开始",
    "rect": [
      883,
      1528,
      951,
      1600
    ],
    "control_type": "Button",
    "window_handle": 65952,
    "window_title": null
  },
  "point": {
    "x": 917,
    "y": 1564,
    "space": "physical-surface"
  },
  "capture": {
    "width": 2560,
    "height": 1600
  },
  "sent_size": {
    "width": 2560,
    "height": 1600
  },
  "image_source": "virtual-desktop|mss",
  "screenshot_sha256": "2cf65dcd47b9cbf2c76998f8d9cbc78b3917dc5599aaaa50cdcf539b8a2ec5b4",
  "result": {
    "ok": true,
    "error_code": null,
    "error_message": null,
    "cursor_readback": [
      917,
      1564
    ],
    "cursor_delta_px": 0
  },
  "rejected_candidates": [],
  "timings_ms": {
    "locate": 168.5056000133045,
    "decision": 0.0,
    "execute_verify": 108.43719998956658
  },
  "timings_share": {
    "locate": 0.6084491093885004,
    "decision": 0.0,
    "execute_verify": 0.39155089061149956
  },
  "action_timing": {
    "pre_move_ms": 60.89689998771064,
    "hold_ms": 46.52539998642169,
    "total_ms": 108.1899999990128,
    "double_click_gap_ms": null,
    "double_click_requested_gap_ms": null,
    "scroll_settle_ms": 0.0,
    "scroll_delta": 0,
    "wait_ms": 0
  },
  "verification": {
    "landing_delta_px": 0.0,
    "landing_verified": true
  },
  "query_scope": "",
  "params": {
    "x": 917,
    "y": 1564,
    "space": "physical-surface"
  },
  "failures": {
    "locate": null,
    "decision": null,
    "execute": null
  },
  "redaction": null,
  "selection_echo": null,
  "allows_visual_fallback": false,
  "channel": "uia",
  "failure_type": "",
  "model_calls": 0,
  "unavailable_capture_sources": {},
  "candidate_limit": 0,
  "candidate_limit_source": "",
  "visual_admission": null,
  "coarse_point": null,
  "crop_plan": null,
  "grounding_echoes": [],
  "fallback": false,
  "fallback_reason": "",
  "fallback_cause": "",
  "gate_hit": false,
  "gate_reasons": [],
  "gate_undecidable": [],
  "refine_enabled": false,
  "state": {
    "before": null,
    "after": null
  },
  "verification_result": null,
  "approval": null,
  "privacy": null,
  "notes": []
}
==================================
```
