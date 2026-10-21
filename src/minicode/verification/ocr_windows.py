"""**Windows 自带的 OCR**（``Windows.Media.Ocr``）—— 工单 M27 的真机那一路。

⚠️ **只读**：它把一帧画面读成文字，**不产生动作、不注入输入**
（工单 M27 AC 7）。它读的是**已经抓好的那一帧**（:class:`CapturedFrame`），
自己不碰桌面。

**为什么走 PowerShell**：Windows 的 OCR 引擎只以 WinRT 的形式暴露，
Python 侧没有对应的投影包。与其给项目引一个新依赖，
不如借系统自带的 ``powershell.exe``（Windows PowerShell 5.1）当那层薄壳 ——
它只做一件事：把 PNG 读成文字。

**后端不可用时**（没有该语言的识别器 / WinRT 装不上 / 识别失败）
→ **抛异常**，上层据此判「判不了」（工单 M27 AC 5），不假装读到了空串。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from minicode.action.image import encode_png
from minicode.action.ports import CapturedFrame

__all__ = ["WindowsOcrPort", "available_languages"]


#: 助手脚本（PowerShell 5.1）。它只读一个 PNG、把识别出的文字写成 UTF-8 文本文件。
#:
#: ⚠️ **三处坑**（都是实测踩出来的）：
#: 1. ``"IAsyncOperation`1"`` 里的反引号在双引号串里会被吃掉 → 用**单引号**；
#: 2. 变量名**不能与参数同名**（PowerShell 变量不分大小写）——
#:    ``$language`` 会把 ``[string]$Language`` 参数按字符串回写，
#:    于是传进 ``TryCreateFromLanguage`` 的变成类型名而不是语言对象；
#: 3. WinRT 的类型要用 ``[类型, 程序集, ContentType = WindowsRuntime]`` 写法解析。
_OCR_SCRIPT = r"""
param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [string]$Language = "zh-Hans-CN"
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($Operation, $ResultType) {
    $method = $asTaskGeneric.MakeGenericMethod($ResultType)
    $task = $method.Invoke($null, @($Operation))
    $task.Wait(-1) | Out-Null
    $task.Result
}

$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$ocrLanguage = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]::new($Language)
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($ocrLanguage)
if ($null -eq $engine) { throw "no OCR engine for language '$Language'" }

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($OutputPath, $result.Text, $utf8)
"""


def _powershell() -> str:
    """``powershell.exe`` 的路径（找不到就抛 —— 不假装有 OCR 后端）。"""
    found = shutil.which("powershell.exe") or shutil.which("powershell")
    if found:
        return found
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = os.path.join(
        system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    if os.path.exists(candidate):
        return candidate
    raise OSError("找不到 powershell.exe（Windows 自带的 OCR 走它当薄壳）")


def available_languages() -> tuple[str, ...]:
    """本机装了哪些 OCR 语言包（读不到就返回空元组 —— 不猜）。"""
    script = (
        "$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, "
        "ContentType = WindowsRuntime]; "
        "[Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages "
        "| ForEach-Object { $_.LanguageTag }"
    )
    try:
        completed = subprocess.run(
            [_powershell(), "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if completed.returncode != 0:
        return ()
    text = completed.stdout.decode("utf-8", errors="replace")
    return tuple(line.strip() for line in text.splitlines() if line.strip())


class WindowsOcrPort:
    """Windows 自带的 OCR 引擎（``Windows.Media.Ocr``）。

    * ``language`` —— 识别语言（本机装了 ``zh-Hans-CN`` / ``en-US`` / ``en-GB``）；
    * ``timeout`` —— 单次识别的超时（秒）—— 超时按「读不出来」处理。
    """

    name = "windows-ocr"

    def __init__(self, language: str = "zh-Hans-CN", *, timeout: float = 30.0):
        self.language = language
        self.timeout = timeout
        #: 读了几次（观测用）。
        self.calls = 0

    def read(self, frame: CapturedFrame) -> str:
        """把这一帧读成文字。**只读**。

        读不出来（后端不可用 / 识别失败 / 超时）就**抛异常** ——
        上层据此判「判不了」（工单 M27 AC 5）。
        """
        if frame.is_empty:
            raise OSError("画面是 0×0（抓不到，没东西可读）")
        self.calls += 1
        png = encode_png(frame)
        with tempfile.TemporaryDirectory(prefix="minicode-ocr-") as workdir:
            image_path = Path(workdir) / "frame.png"
            output_path = Path(workdir) / "text.txt"
            script_path = Path(workdir) / "ocr.ps1"
            image_path.write_bytes(png)
            script_path.write_text(_OCR_SCRIPT, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [
                        _powershell(),
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script_path),
                        "-ImagePath",
                        str(image_path),
                        "-OutputPath",
                        str(output_path),
                        "-Language",
                        self.language,
                    ],
                    capture_output=True,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise OSError(f"OCR 超时（{self.timeout} 秒）") from exc
            except OSError as exc:
                raise OSError(f"OCR 后端跑不起来：{exc}") from exc
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace").strip()
                raise OSError(f"OCR 后端失败：{detail or completed.returncode}")
            if not output_path.exists():
                raise OSError("OCR 后端没有给出读数")
            return output_path.read_text(encoding="utf-8", errors="replace")
