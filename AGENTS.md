# AGENTS.md

Working agreements for agents on this repo.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for `xiaochuan7/minicode` (private), driven through the `gh` CLI — which is **not on PATH** here, call it as `& "$env:ProgramFiles\GitHub CLI\gh.exe"`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default roles, used verbatim: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix` — already created in the repo. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one root `CONTEXT.md` plus `docs/adr/`. See `docs/agents/domain.md`.

## 仓库速览

minicode = Windows 桌面 computer-use agent。架构定稿见 `docs/research/01-架构决策备忘.md`，本机实测事实见 `docs/01-现状.md`，现状总览见 `docs/00-现状.md`。

- 坐标契约：模型端 0~1000 归一化，工程端全程物理像素；最终落点只在 crop 上产生（`docs/00-现状.md` §3）。
- 三个原型已验证、冻结不改：`src/minicode/coord`、`src/minicode/exec`、`src/minicode/verify`。

```powershell
cd src/minicode\coord  && python -m unittest test_coordinates
cd ..\exec             && python selftest.py
cd ..\verify           && python test_01_text_and_probe.py   # 真调 API，消耗额度
```
