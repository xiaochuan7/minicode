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
from dataclasses import dataclass
from pathlib import Path

from minicode.action.image import encode_png
from minicode.action.ports import CapturedFrame

__all__ = ["OcrWord", "WindowsOcrPort", "available_languages", "parse_words"]


@dataclass(frozen=True)
class OcrWord:
    """**一个词**的识别结果（框 = 物理像素，相对喂进去的那张 PNG）。

    * ``text`` —— 认出来的词；
    * ``x`` / ``y`` —— 词框左上角（那张 PNG 的像素）；
    * ``w`` / ``h`` —— 词框的宽高（那张 PNG 的像素）。

    ⚠️ **坐标是「那张图」的像素**，不是虚拟桌面坐标：那张图就是抓屏帧本身
    （原始物理分辨率，1 像素 = 1 物理像素），所以**加上帧的原点**
    （:attr:`minicode.action.ports.CapturedFrame.left` / ``top``）才是
    物理表面坐标 —— 那是唯一的真值空间。
    """

    text: str
    x: int
    y: int
    w: int
    h: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        """词框 ``(left, top, right, bottom)``（那张图的像素）。"""
        return (self.x, self.y, self.x + self.w, self.y + self.h)


#: 助手脚本（PowerShell 5.1）。它只读一个 PNG、把识别出的文字**与词级框**
#: 写成两个 UTF-8 文本文件。
#:
#: ⚠️ **三处坑**（都是实测踩出来的）：
#: 1. ``"IAsyncOperation`1"`` 里的反引号在双引号串里会被吃掉 → 用**单引号**；
#: 2. 变量名**不能与参数同名**（PowerShell 变量不分大小写）——
#:    ``$language`` 会把 ``[string]$Language`` 参数按字符串回写，
#:    于是传进 ``TryCreateFromLanguage`` 的变成类型名而不是语言对象；
#: 3. WinRT 的类型要用 ``[类型, 程序集, ContentType = WindowsRuntime]`` 写法解析。
#:
#: **词级框**（B-1）：遍历 ``$result.Lines[].Words[]``，每个词输出
#: ``text\tx\ty\tw\th``（**物理像素**，相对喂进去的那张 PNG）。
#: ``OcrWord.BoundingRect`` 的定义原文："Gets the position and size in pixels
#: of the recognized word from the top left corner of image"。
_OCR_SCRIPT = r"""
param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$WordsOutputPath,
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

# -- 词级框（B-1）：每行一个词，字段之间用制表符 ----------------------------
$wordLines = New-Object System.Collections.Generic.List[string]
$wordCount = 0
foreach ($line in $result.Lines) {
    foreach ($word in $line.Words) {
        $text = [string]$word.Text
        if ([string]::IsNullOrWhiteSpace($text)) { continue }
        # 制表符/换行会破坏字段切分 → 换成空格。
        $text = $text -replace "`t", " " -replace "`r", " " -replace "`n", " "
        $rect = $word.BoundingRect
        $wordLines.Add(("{0}`t{1}`t{2}`t{3}`t{4}" -f $text,
            [int][double]$rect.X, [int][double]$rect.Y,
            [int][double]$rect.Width, [int][double]$rect.Height))
        $wordCount++
    }
}
$wordLines.Insert(0, ("OCR_WORDS_V1`t{0}" -f $wordCount))
[System.IO.File]::WriteAllText($WordsOutputPath, ($wordLines -join "`n"), $utf8)
"""

#: 词级读数的**文件头**（解析时先认它，认不出就报「读不出」而不是「读到空」）。
_WORDS_HEADER = "OCR_WORDS_V1"


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

        ⚠️ 这是**整段文本**（原有行为不变）；要**词级框**用 :meth:`read_words`。
        """
        text, _words = self._invoke(frame)
        return text

    def read_words(self, frame: CapturedFrame) -> list[OcrWord]:
        """把这一帧读成**词级框**。**只读**。

        每个词的框是**喂进去的那张 PNG 的像素**（= 原始物理分辨率，1 像素 = 1 物理像素）；
        要变成物理表面坐标，**加上帧的原点**（``frame.left`` / ``frame.top``）。

        读不出来（后端不可用 / 识别失败 / 超时 / 读数格式不对）就**抛异常** ——
        上层据此判「判不了」，**不假装读到空**（工单 M27 AC 5 的口径）。

        「图上没有文字」是**合法的空结果**（返回空列表）：那与「读不出来」是两件事。
        """
        _text, words = self._invoke(frame)
        if words is None:
            raise OSError("OCR 后端没有给出词级读数")
        return parse_words(words)

    def _invoke(self, frame: CapturedFrame) -> tuple[str, str | None]:
        """跑一次后端 → ``(整段文本, 词级读数原文)``。

        词级读数为 ``None`` = 后端没写那个文件（**不是**「读到空」）。
        """
        if frame.is_empty:
            raise OSError("画面是 0×0（抓不到，没东西可读）")
        self.calls += 1
        png = encode_png(frame)
        with tempfile.TemporaryDirectory(prefix="minicode-ocr-") as workdir:
            image_path = Path(workdir) / "frame.png"
            output_path = Path(workdir) / "text.txt"
            words_path = Path(workdir) / "words.tsv"
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
                        "-WordsOutputPath",
                        str(words_path),
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
            words = None
            if words_path.exists():
                try:
                    words = words_path.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise OSError(f"OCR 词级读数不是 UTF-8：{exc}") from exc
            return output_path.read_text(encoding="utf-8", errors="replace"), words


def parse_words(raw: str) -> list[OcrWord]:
    """词级读数（制表符分隔）→ :class:`OcrWord` 列表。

    **解析必须稳健**：读不出来（头不对 / 条数对不上 / 字段不是整数）就**抛异常** ——
    上层据此判「判不了」，**不假装读到空**。
    """
    lines = raw.split("\n")
    if not lines or not lines[0].strip():
        raise OSError("OCR 词级读数是空的（后端没有给出读数）")
    header = lines[0].rstrip("\r").split("\t")
    if len(header) != 2 or header[0] != _WORDS_HEADER:
        raise OSError(f"OCR 词级读数的头不对：{lines[0][:80]!r}")
    try:
        declared = int(header[1])
    except ValueError as exc:
        raise OSError(f"OCR 词级读数声明的条数不是整数：{header[1]!r}") from exc
    body = [line.rstrip("\r") for line in lines[1:] if line.strip()]
    if len(body) != declared:
        raise OSError(
            f"OCR 词级读数的条数与声明不符：声明 {declared}，实际 {len(body)}"
        )
    words: list[OcrWord] = []
    for line in body:
        fields = line.split("\t")
        if len(fields) != 5:
            raise OSError(f"OCR 词级读数这一行解析不了：{line[:80]!r}")
        text, x, y, w, h = fields
        try:
            words.append(
                OcrWord(text=text, x=int(x), y=int(y), w=int(w), h=int(h))
            )
        except ValueError as exc:
            raise OSError(f"OCR 词级读数的坐标不是整数：{line[:80]!r}") from exc
    return words
