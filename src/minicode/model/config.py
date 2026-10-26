"""仓库配置：**凭据的唯一来源**。

本项目只从**仓库里的 ``.env``** 读端点、密钥、模型、协议、会话标识、两个兼容头
（``user-agent`` 与 ``x-opencode-client``）与**采样温度**。
用户目录下的凭据文件**不是**任何主链路代码的输入 —— 换机器、换人只改配置不改代码。

三条硬口径：

* **配置项缺失或为空 → 显式报错并点名缺哪一项**，不静默回退默认值；
  （例外：``MINICODE_SESSION_ID`` / ``MINICODE_HTTP_USER_AGENT`` /
  ``MINICODE_HTTP_CLIENT`` / ``MINICODE_TEMPERATURE`` 是**可选**的，见下）
* **端点按协议拼**：``anthropic`` → ``{base_url}/v1/messages``；
  ``openai`` → ``{base_url}/chat/completions``。不再要求内部网关的 ``/go`` 后缀 ——
  通用 provider（api.anthropic.com、api.deepseek.com …）不能被它挡住；
* **只认仓库根目录下那一个 ``.env``** —— 不从当前工作目录向上找，
  否则会读到仓库外的 ``.env``（那是第二个凭据来源）。

**两套配置键并存**（新键优先，旧键继续可用，便于老用户平滑迁移）：

* 新（通用 provider）：``MINICODE_PROVIDER`` / ``MINICODE_BASE_URL`` / ``MINICODE_API_KEY``；
* 旧（Anthropic 时代）：``MINICODE_ANTHROPIC_BASE_URL`` / ``MINICODE_ANTHROPIC_API_KEY``；
* ``MINICODE_MODEL`` 两套共用。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ModelConfigError

__all__ = [
    "DEFAULT_TEMPERATURE",
    "DEFAULT_USER_AGENT",
    "ENDPOINT_PATHS",
    "FREE_TIER_PATH_SUFFIX",
    "GENERIC_CONFIG_KEYS",
    "LEGACY_CONFIG_KEYS",
    "OPTIONAL_CONFIG_KEYS",
    "PROTOCOLS",
    "PROTOCOL_ANTHROPIC",
    "PROTOCOL_OPENAI",
    "ModelConfig",
    "find_env_file",
    "load_config",
    "parse_env_file",
    "resolve_endpoint",
]

#: 协议名（``ModelConfig.protocol`` 的允许值，也是 ``MINICODE_PROVIDER`` 的允许值）。
PROTOCOL_ANTHROPIC = "anthropic"
PROTOCOL_OPENAI = "openai"

#: 允许的协议（非法值报错时按这个列表列出）。
PROTOCOLS: tuple[str, ...] = (PROTOCOL_ANTHROPIC, PROTOCOL_OPENAI)

#: 协议 → 端点路径（拼在 ``base_url`` 后面）。
ENDPOINT_PATHS: dict[str, str] = {
    PROTOCOL_ANTHROPIC: "/v1/messages",
    PROTOCOL_OPENAI: "/chat/completions",
}

#: **新键**（通用 provider）→ 字段名。旧键见 :data:`LEGACY_CONFIG_KEYS`。
GENERIC_CONFIG_KEYS: dict[str, str] = {
    "protocol": "MINICODE_PROVIDER",
    "base_url": "MINICODE_BASE_URL",
    "api_key": "MINICODE_API_KEY",
    "model": "MINICODE_MODEL",
}

#: **旧键**（Anthropic 时代）→ 字段名。新键存在时新键优先。
LEGACY_CONFIG_KEYS: dict[str, str] = {
    "base_url": "MINICODE_ANTHROPIC_BASE_URL",
    "api_key": "MINICODE_ANTHROPIC_API_KEY",
}

#: **可选**配置项名 → 仓库 ``.env`` 里的键名（缺项 → 用缺省值，不报错）。
OPTIONAL_CONFIG_KEYS: dict[str, str] = {
    "session_id": "MINICODE_SESSION_ID",
    "user_agent": "MINICODE_HTTP_USER_AGENT",
    "client_header": "MINICODE_HTTP_CLIENT",
    "temperature": "MINICODE_TEMPERATURE",
}

#: ``user-agent`` 的缺省值（通用 provider 不该被内部网关的头绑死）。
DEFAULT_USER_AGENT = "minicode/0.1.0"

#: 采样温度的缺省值（**关采样**）。
#:
#: ⚠️ **为什么必须是 0**：不显式给就走服务端缺省（常见缺省 1.0）→ 有采样 →
#: 同图同提示词两次回答可能不同（本仓 183 条那轮的跨运行命中翻转 16.7%~26.2%）。
DEFAULT_TEMPERATURE = 0.0

#: ⚠️ **已废弃**：内部网关的免费档通道后缀。
#:
#: :func:`resolve_endpoint` **不再强制**这个后缀（通用 provider 的端点会被它挡住）。
#: 常量保留只有一个原因：``minicode.model.regressions``（内部网关的真调 API 回归）
#: 还在 import 它；那个脚本不在发布范围内，待删除/改写。
FREE_TIER_PATH_SUFFIX = "/go"

_PACKAGE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ModelConfig:
    """模型调用的配置（端点/密钥/模型必填；会话标识与两个兼容头可选）。"""

    base_url: str
    api_key: str
    model: str
    #: 会话标识 —— **可选**，空 = 不发 ``x-opencode-session``。
    session_id: str = ""
    #: ``user-agent`` —— **可选**，缺省 :data:`DEFAULT_USER_AGENT`。
    user_agent: str = DEFAULT_USER_AGENT
    #: 网关兼容头 ``x-opencode-client`` —— **可选**，空 = 不发。
    client_header: str = ""
    #: **采样温度**（请求体里显式带上；缺省 0 = 关采样）。
    #:
    #: 可由 ``.env`` 的 ``MINICODE_TEMPERATURE`` 覆盖（**可选**，缺省 0）。
    #: 0 之外的值是**故意留的逃生口**（服务端若不支持 temperature=0，可临时调高），
    #: 但**缺省必须关采样** —— 否则同图同提示词的两次回答可能不同。
    temperature: float = DEFAULT_TEMPERATURE
    #: 协议：``anthropic``（Anthropic Messages）| ``openai``（OpenAI 兼容）。
    #:
    #: 由 ``.env`` 的 ``MINICODE_PROVIDER`` 指定；缺省 ``anthropic``（向后兼容）。
    protocol: str = PROTOCOL_ANTHROPIC
    #: 字段 → **实际生效的 env 键名**（如 ``{"api_key": "MINICODE_API_KEY"}``）。
    #:
    #: 用于启动日志说清「这个值到底从哪个键来」；缺省值的字段对应空串。
    origins: Mapping[str, str] = field(default_factory=dict)
    #: 配置来源文件路径（写进请求日志，便于事后追溯是哪份配置）。
    source: str = ""

    def __post_init__(self) -> None:
        if self.protocol not in PROTOCOLS:
            raise ModelConfigError(
                f"协议 {self.protocol!r} 不在允许值里（{' / '.join(PROTOCOLS)}）；"
                f"请用 {GENERIC_CONFIG_KEYS['protocol']} 指定"
            )

    @property
    def endpoint(self) -> str:
        """实际请求的端点（按 :attr:`protocol` 拼路径）。"""
        return resolve_endpoint(self.base_url, self.protocol)

    def describe(self) -> str:
        """启动日志用的一行描述（**脱敏**：api_key 只留前 4 后 4）。"""
        source = self.origins.get("api_key") or "缺省"
        return (
            f"模型配置：protocol={self.protocol} endpoint={self.endpoint} "
            f"model={self.model} api_key={_mask_secret(self.api_key)}"
            f"（来源 {source}）"
        )


def _mask_secret(secret: str) -> str:
    """密钥脱敏：只留前 4 后 4；太短就整体隐去（不泄露长度之外的任何字符）。"""
    if not secret:
        return "（未配置）"
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}****{secret[-4:]}"


def resolve_endpoint(base_url: str, protocol: str = PROTOCOL_ANTHROPIC) -> str:
    """把基础地址解析成请求端点（按协议拼路径）。

    * ``anthropic`` → ``{base_url}/v1/messages``；
    * ``openai`` → ``{base_url}/chat/completions``。

    容忍尾部 ``/``；基础地址为空 → :class:`ModelConfigError`。
    **不再校验内部网关后缀** —— 通用端点（``https://api.anthropic.com``、
    ``https://api.deepseek.com`` …）都能直接用。
    """
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        raise ModelConfigError(
            "基础地址为空：仓库配置里没有填端点"
            f"（新键 {GENERIC_CONFIG_KEYS['base_url']} 或旧键 "
            f"{LEGACY_CONFIG_KEYS['base_url']}）"
        )
    path = ENDPOINT_PATHS.get(protocol)
    if path is None:
        raise ModelConfigError(
            f"协议 {protocol!r} 不在允许值里（{' / '.join(PROTOCOLS)}）"
        )
    return f"{cleaned}{path}"


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

    缺项或空值 → :class:`ModelConfigError`，消息里点名缺哪一项；
    端点 / 密钥两套键**任一有值即可**（新键优先），两边都缺时**两个键都点名**。
    """
    path = Path(env_file) if env_file is not None else find_env_file()
    if not path.is_file():
        raise ModelConfigError(f"仓库配置文件不存在：{path}")
    values = parse_env_file(path)

    protocol, protocol_origin = _protocol_of(values)
    base_url, base_url_origin = _pick_configured_key(values, "base_url")
    api_key, api_key_origin = _pick_configured_key(values, "api_key")
    model = values.get(GENERIC_CONFIG_KEYS["model"], "").strip()

    missing: list[str] = []
    if not base_url:
        missing.append(
            f"{GENERIC_CONFIG_KEYS['base_url']}"
            f"（旧键 {LEGACY_CONFIG_KEYS['base_url']} 也可）"
        )
    if not api_key:
        missing.append(
            f"{GENERIC_CONFIG_KEYS['api_key']}"
            f"（旧键 {LEGACY_CONFIG_KEYS['api_key']} 也可）"
        )
    if not model:
        missing.append(GENERIC_CONFIG_KEYS["model"])
    if missing:
        raise ModelConfigError(
            f"仓库配置 {path} 缺少或为空：{'；'.join(missing)}；"
            "这些项没有默认值，必须显式填（新键优先，旧键继续可用）"
        )

    session_id, session_origin = _optional_value(values, "session_id")
    user_agent_raw, user_agent_origin = _optional_value(values, "user_agent")
    client_header, client_header_origin = _optional_value(values, "client_header")
    temperature = _temperature_of(values, path)
    temperature_origin = ""
    if values.get(OPTIONAL_CONFIG_KEYS["temperature"], "").strip():
        temperature_origin = OPTIONAL_CONFIG_KEYS["temperature"]

    config = ModelConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        session_id=session_id,
        user_agent=user_agent_raw or DEFAULT_USER_AGENT,
        client_header=client_header,
        temperature=temperature,
        protocol=protocol,
        origins={
            "protocol": protocol_origin,
            "base_url": base_url_origin,
            "api_key": api_key_origin,
            "model": GENERIC_CONFIG_KEYS["model"],
            "session_id": session_origin,
            "user_agent": user_agent_origin,
            "client_header": client_header_origin,
            "temperature": temperature_origin,
        },
        source=str(path),
    )
    # 端点在装载时就拼一次（别等到发请求才发现协议/地址对不上）。
    resolve_endpoint(config.base_url, config.protocol)
    return config


def _protocol_of(values: Mapping[str, str]) -> tuple[str, str]:
    """协议：**可选**，缺省 ``anthropic``；给了就必须是允许值之一。"""
    key = GENERIC_CONFIG_KEYS["protocol"]
    raw = values.get(key, "").strip()
    if not raw:
        return PROTOCOL_ANTHROPIC, ""
    if raw not in PROTOCOLS:
        raise ModelConfigError(
            f"仓库配置的 {key}={raw!r} 不是允许的协议（{' / '.join(PROTOCOLS)}）"
        )
    return raw, key


def _pick_configured_key(values: Mapping[str, str], field_name: str) -> tuple[str, str]:
    """取一个必填字段：**新键优先，旧键兜底**；返回 ``(值, 实际生效的键名)``。"""
    new_key = GENERIC_CONFIG_KEYS[field_name]
    new_value = values.get(new_key, "").strip()
    if new_value:
        return new_value, new_key
    legacy_key = LEGACY_CONFIG_KEYS.get(field_name)
    if legacy_key:
        legacy_value = values.get(legacy_key, "").strip()
        if legacy_value:
            return legacy_value, legacy_key
    return "", ""


def _optional_value(values: Mapping[str, str], field_name: str) -> tuple[str, str]:
    """取一个**可选**字段；返回 ``(值, 实际生效的键名)``，缺省时键名为空串。"""
    key = OPTIONAL_CONFIG_KEYS[field_name]
    value = values.get(key, "").strip()
    return value, (key if value else "")


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
