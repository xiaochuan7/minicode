"""仓库配置：**凭据的唯一来源**。

本项目只从**仓库里的 ``.env``** 读端点、密钥、模型、会话标识、两个头
（``user-agent`` 与 ``x-opencode-client``）与**采样温度**。
用户目录下的凭据文件**不是**任何主链路代码的输入 —— 换机器、换人只改配置不改代码。

三条硬口径：

* **配置项缺失或为空 → 显式报错并点名缺哪一项**，不静默回退默认值；
  （唯一例外：``MINICODE_TEMPERATURE`` 是**可选**的 —— 缺省 0，见下）
* **免费档只能走带 ``/go`` 后缀的那条通道**（仓库配置模板里两条地址都在，
  「选哪条」因此必须是**可测行为**，不是靠人记）；
* **只认仓库根目录下那一个 ``.env``** —— 不从当前工作目录向上找，
  否则会读到仓库外的 ``.env``（那是第二个凭据来源）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ModelConfigError

__all__ = [
    "DEFAULT_TEMPERATURE",
    "FREE_TIER_PATH_SUFFIX",
    "OPTIONAL_CONFIG_KEYS",
    "REQUIRED_CONFIG_KEYS",
    "ModelConfig",
    "find_env_file",
    "load_config",
    "parse_env_file",
    "resolve_endpoint",
]

#: 免费档通道的后缀。免费档模型**只能**走带这个后缀的基础地址。
FREE_TIER_PATH_SUFFIX = "/go"

#: 配置项名 → 仓库 ``.env`` 里的键名。六个都在配置模板里。
REQUIRED_CONFIG_KEYS: dict[str, str] = {
    "base_url": "MINICODE_ANTHROPIC_BASE_URL",
    "api_key": "MINICODE_ANTHROPIC_API_KEY",
    "model": "MINICODE_MODEL",
    "session_id": "MINICODE_SESSION_ID",
    "user_agent": "MINICODE_HTTP_USER_AGENT",
    "client_header": "MINICODE_HTTP_CLIENT",
}

#: **可选**配置项名 → 仓库 ``.env`` 里的键名（缺项 → 用缺省值，不报错）。
OPTIONAL_CONFIG_KEYS: dict[str, str] = {
    "temperature": "MINICODE_TEMPERATURE",
}

#: 采样温度的缺省值（**关采样**）。
#:
#: ⚠️ **为什么必须是 0**：不显式给就走服务端缺省（常见缺省 1.0）→ 有采样 →
#: 同图同提示词两次回答可能不同（本仓 183 条那轮的跨运行命中翻转 16.7%~26.2%，
#: 见 ``docs/research/vision-root-cause.md``）。
DEFAULT_TEMPERATURE = 0.0

_PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ModelConfig:
    """模型调用的配置（六项全部来自仓库 ``.env``）。"""

    base_url: str
    api_key: str
    model: str
    session_id: str
    user_agent: str
    client_header: str
    #: **采样温度**（请求体里显式带上；缺省 0 = 关采样）。
    #:
    #: 可由 ``.env`` 的 ``MINICODE_TEMPERATURE`` 覆盖（**可选**，缺省 0）。
    #: 0 之外的值是**故意留的逃生口**（服务端若不支持 temperature=0，可临时调高），
    #: 但**缺省必须关采样** —— 否则同图同提示词的两次回答可能不同。
    temperature: float = DEFAULT_TEMPERATURE
    #: 配置来源文件路径（写进请求日志，便于事后追溯是哪份配置）。
    source: str = ""

    @property
    def endpoint(self) -> str:
        """实际请求的端点。"""
        return resolve_endpoint(self.base_url)


def resolve_endpoint(base_url: str) -> str:
    """把基础地址解析成请求端点，并**校验它是不是免费档那条通道**。

    规则（可测行为）：免费档模型只能走带 ``/go`` 后缀的基础地址；
    指到别处就**显式报错**，而不是「照样发出去、让服务端回一个 FreeTierError」。
    """
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        raise ModelConfigError("基础地址为空：仓库配置里没有填端点")
    if not cleaned.endswith(FREE_TIER_PATH_SUFFIX):
        raise ModelConfigError(
            f"免费档模型只能走带 {FREE_TIER_PATH_SUFFIX} 后缀的通道，当前基础地址是 {cleaned}；"
            f"请把它改成仓库配置里那条免费档地址"
        )
    return f"{cleaned}/v1/messages"


def repo_root() -> Path:
    """仓库根目录（从包所在目录向上找带 ``.git`` 或 ``pyproject.toml`` 的那一层）。"""
    for directory in [_PACKAGE_DIR, *_PACKAGE_DIR.parents]:
        if (directory / ".git").exists() or (directory / "pyproject.toml").is_file():
            return directory
    raise ModelConfigError(
        f"找不到仓库根目录（从 {_PACKAGE_DIR} 向上找过 .git / pyproject.toml）；"
        "仓库配置 .env 必须跟着仓库走"
    )


def find_env_file(start: Path | None = None) -> Path:
    """找仓库的 ``.env``。

    给了 ``start`` 就用它（必须是文件）；否则用**仓库根目录下那一个** ``.env``。
    **不从当前工作目录向上找** —— 那会读到仓库外的 ``.env``。
    """
    if start is not None:
        path = Path(start).resolve()
        if not path.is_file():
            raise ModelConfigError(f"仓库配置文件不存在：{path}")
        return path
    return repo_root() / ".env"


def parse_env_file(path: Path) -> dict[str, str]:
    """解析 ``.env``：``KEY=VALUE``，忽略注释与空行，去掉成对的引号。"""
    values: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_config(env_file: Path | None = None) -> ModelConfig:
    """从仓库 ``.env`` 装载配置。

    缺项或空值 → :class:`ModelConfigError`，消息里点名缺哪一项。
    """
    path = Path(env_file) if env_file is not None else find_env_file()
    if not path.is_file():
        raise ModelConfigError(f"仓库配置文件不存在：{path}")
    values = parse_env_file(path)

    missing = [
        key for key in REQUIRED_CONFIG_KEYS.values() if not values.get(key, "").strip()
    ]
    if missing:
        raise ModelConfigError(
            f"仓库配置 {path} 缺少或为空：{'、'.join(missing)}；"
            "这些项没有默认值，必须显式填"
        )

    config = ModelConfig(
        base_url=values[REQUIRED_CONFIG_KEYS["base_url"]].strip(),
        api_key=values[REQUIRED_CONFIG_KEYS["api_key"]].strip(),
        model=values[REQUIRED_CONFIG_KEYS["model"]].strip(),
        session_id=values[REQUIRED_CONFIG_KEYS["session_id"]].strip(),
        user_agent=values[REQUIRED_CONFIG_KEYS["user_agent"]].strip(),
        client_header=values[REQUIRED_CONFIG_KEYS["client_header"]].strip(),
        temperature=_temperature_of(values, path),
        source=str(path),
    )
    # 通道对不对，装载时就判一次（别等到发请求才发现指错了）。
    resolve_endpoint(config.base_url)
    return config


def _temperature_of(values: Mapping[str, str], path: Path) -> float:
    """采样温度：**可选**，缺省 0（关采样）；给了就必须是数字。

    给了但不是数字 → :class:`ModelConfigError`（**不静默回退** ——
    悄悄按 0 跑会让「配置到底生效没有」这个问题永远查不出来）。
    """
    key = OPTIONAL_CONFIG_KEYS["temperature"]
    raw = values.get(key, "").strip()
    if not raw:
        return DEFAULT_TEMPERATURE
    try:
        return float(raw)
    except ValueError as exc:
        raise ModelConfigError(
            f"仓库配置 {path} 的 {key}={raw!r} 不是数字（缺省应为 {DEFAULT_TEMPERATURE}）"
        ) from exc
