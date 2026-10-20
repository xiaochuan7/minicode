---
status: accepted
---

# 用 Python 而不是 TypeScript 实现

minicode 的实现语言定为 **Python**。理由：作为骨架候选的开源 harness（UFO²/UFO³）本身就是 Python，Windows 的控件树绑定与输入注入在 Python 生态里最成熟（截图、可访问性树、输入注入、图像处理都有成熟绑定），而且三个已通过本机实测的原型也已经在 Python 里。换 TypeScript 会同时失去现成骨架与最厚的 Windows 生态，还要重写已验证的原型。

## Consequences

- Windows 平台能力通过 Python 绑定与 `ctypes` 访问，而不是通过 Node 原生模块。
- 核心链路的依赖保持为零第三方包，第三方能力按需安装。
