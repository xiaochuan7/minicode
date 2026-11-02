"""ScreenParser 权重发现与下载（诊断脚本，不入交付）。"""
import os
import sys

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from huggingface_hub import hf_hub_download, list_repo_files  # noqa: E402

REPO = "docling-project/ScreenParser"
files = list_repo_files(REPO)
print("repo files:", files, flush=True)
candidates = [f for f in files if f.endswith((".pt", ".onnx", ".safetensors"))]
print("weight candidates:", candidates, flush=True)
for name in candidates:
    path = hf_hub_download(REPO, name)
    print("downloaded:", name, "->", path, flush=True)
