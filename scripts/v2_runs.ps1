$ErrorActionPreference = "Continue"
$env:PYTHONPATH = "src"
Set-Location "C:\Users\22197\Desktop\minicode"

$MEMO = "runs/v2-memo/memo.jsonl"
New-Item -ItemType Directory -Force -Path "runs/v2-memo" | Out-Null

function Run-Eval($name, $extra) {
    Write-Host "=== $name 开始 $(Get-Date -Format HH:mm:ss) ==="
    $out = "runs/$name"
    python -m minicode.eval --dataset .scratch/sspro --out $out --workers 4 --memo-cache $MEMO @extra 2>&1 |
        Tee-Object -FilePath "$out.log"
    Write-Host "=== $name 结束 $(Get-Date -Format HH:mm:ss) ==="
}

# C: 对照臂 —— 2016 档，不放大，一次一问（= T2 之后的现状）
Run-Eval "v2-eval-2016-control" @("--upscale-target-px", "0")

# B: T3 单独 —— 2016 档 + crop 放大，一次一问
Run-Eval "v2-eval-2016-upscale" @("--upscale-target-px", "30")

# A: T3 + T4 —— 2016 档 + 放大 + K=8 多候选 + 标记图选择 + 整屏再搜
Run-Eval "v2-eval-2016-multi" @("--upscale-target-px", "30", "--variants", "8", "--pool", "runs/v2-eval-2016-multi/pool.jsonl")

# E: T1 采样池探针 —— 粗定位与精定位各采 8 次，量 oracle-of-8
Run-Eval "s6-vote-probe" @("--limit", "40", "--upscale-target-px", "30", "--variants", "8", "--coarse-variants", "8", "--pool", "runs/s6-vote-probe/pool.jsonl")
# D: 1536 对照臂（档位对照；最后跑，时间不够可弃）
Run-Eval "v2-eval-1536-control" @("--model-input", "1536x960", "--upscale-target-px", "0")

Write-Host "=== 全部结束 $(Get-Date -Format HH:mm:ss) ==="
