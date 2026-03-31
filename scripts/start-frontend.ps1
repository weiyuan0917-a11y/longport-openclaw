<#
.SYNOPSIS
  启动 Next.js 开发服务器（npm run dev），日志追加到 logs/frontend-*.log。
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $LogDir "frontend-$ts.log"
Set-Location (Join-Path $Root "frontend")

Write-Host "Log: $LogFile"
npm run dev 2>&1 | Tee-Object -FilePath $LogFile -Append
