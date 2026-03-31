<#

.SYNOPSIS

  启动 FastAPI：默认「小生产」—— 无 --reload，监听 127.0.0.1；加 -Dev 为本地开发（0.0.0.0 + --reload）。

  实际参数由 scripts/run_api.py + 仓库根 backend_uvicorn_spec.py 统一生成。

  标准输出与错误会追加写入 logs/api-*.log。

#>

param(

    [switch]$Dev,

    [string]$BindHost = "",

    [int]$Port = 8000

)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$LogDir = Join-Path $Root "logs"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$ts = Get-Date -Format "yyyyMMdd-HHmmss"

$LogFile = Join-Path $LogDir "api-$ts.log"

$env:PYTHONPATH = $Root

Set-Location $Root



$runApi = Join-Path $Root "scripts\run_api.py"

$runArgs = @($runApi)

if ($Dev) { $runArgs += "--dev" }

if ($BindHost) { $runArgs += "--host", $BindHost }

$runArgs += "--port", "$Port"



Write-Host "PYTHONPATH=$Root"

Write-Host "run_api: python $($runArgs -join ' ')"

Write-Host "Log: $LogFile"



python @runArgs 2>&1 | Tee-Object -FilePath $LogFile -Append


