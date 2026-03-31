param(
    [switch]$PreviewOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-PathSizeBytes {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0L }
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) { return [int64]$item.Length }
    $sum = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return 0L }
    return [int64]$sum
}

function Format-Size {
    param([Parameter(Mandatory = $true)][int64]$Bytes)
    if ($Bytes -ge 1GB) { return ("{0:N2} GB" -f ($Bytes / 1GB)) }
    return ("{0:N1} MB" -f ($Bytes / 1MB))
}

function Ask-YesNo {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [bool]$DefaultNo = $true
    )
    $suffix = if ($DefaultNo) { "[y/N]" } else { "[Y/n]" }
    $ans = (Read-Host "$Prompt $suffix").Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($ans)) { return (-not $DefaultNo) }
    return $ans -in @("y", "yes")
}

$root = (Get-Location).Path
Write-Host ""
Write-Host "=== LongPort 项目半自动清理 ===" -ForegroundColor Cyan
Write-Host "项目目录: $root"
Write-Host ""

$targets = @(
    @{ Name = ".next 构建缓存"; Path = "frontend\.next"; Type = "path" },
    @{ Name = "后端打包中间产物"; Path = "build"; Type = "path" },
    @{ Name = "运行日志目录"; Path = "logs"; Type = "path" },
    @{ Name = "根 node_modules"; Path = "node_modules"; Type = "path" },
    @{ Name = "前端 node_modules"; Path = "frontend\node_modules"; Type = "path" },
    @{ Name = "旧打包产物 dist"; Path = "dist"; Type = "path" },
    @{ Name = "多余 venv: .launcher-venv"; Path = ".launcher-venv"; Type = "path" },
    @{ Name = "多余 venv: .openbb-venv"; Path = ".openbb-venv"; Type = "path" },
    @{ Name = "Python 缓存目录 (__pycache__)"; Path = "__pycache__"; Type = "pattern-dir" },
    @{ Name = "Python 字节码文件 (*.pyc)"; Path = "*.pyc"; Type = "pattern-file" }
)

$choices = @()

foreach ($t in $targets) {
    $exists = $false
    [int64]$size = 0

    switch ($t.Type) {
        "path" {
            $exists = Test-Path -LiteralPath $t.Path
            if ($exists) { $size = Get-PathSizeBytes -Path $t.Path }
        }
        "pattern-dir" {
            $dirs = Get-ChildItem -Recurse -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq "__pycache__" }
            $exists = ($dirs.Count -gt 0)
            if ($exists) {
                foreach ($d in $dirs) {
                    $size += Get-PathSizeBytes -Path $d.FullName
                }
            }
        }
        "pattern-file" {
            $files = Get-ChildItem -Recurse -File -Force -Filter "*.pyc" -ErrorAction SilentlyContinue
            $exists = ($files.Count -gt 0)
            if ($exists) {
                $sum = ($files | Measure-Object -Property Length -Sum).Sum
                if ($null -ne $sum) { $size = [int64]$sum }
            }
        }
    }

    if (-not $exists) {
        Write-Host ("[SKIP] {0} (不存在)" -f $t.Name) -ForegroundColor DarkGray
        continue
    }

    $line = "{0}  ->  {1}" -f $t.Name, (Format-Size -Bytes $size)
    $pick = Ask-YesNo -Prompt ("清理 " + $line + " ?")
    if ($pick) {
        $choices += [pscustomobject]@{
            Name = $t.Name
            Path = $t.Path
            Type = $t.Type
            Size = $size
        }
    }
}

Write-Host ""
if ($choices.Count -eq 0) {
    Write-Host "未勾选任何清理项，已退出。" -ForegroundColor Yellow
    exit 0
}

[int64]$total = ($choices | Measure-Object -Property Size -Sum).Sum
Write-Host "你已勾选以下项目：" -ForegroundColor Green
$choices | ForEach-Object {
    Write-Host (" - {0} ({1})" -f $_.Name, (Format-Size -Bytes $_.Size))
}
Write-Host ("预计可释放: {0}" -f (Format-Size -Bytes $total)) -ForegroundColor Green
Write-Host ""

if ($PreviewOnly) {
    Write-Host "PreviewOnly 模式：仅预览，不执行删除。" -ForegroundColor Yellow
    exit 0
}

$final = Ask-YesNo -Prompt "确认执行删除？此操作不可恢复。" -DefaultNo:$true
if (-not $final) {
    Write-Host "已取消执行。" -ForegroundColor Yellow
    exit 0
}

foreach ($c in $choices) {
    try {
        switch ($c.Type) {
            "path" {
                if (Test-Path -LiteralPath $c.Path) {
                    Remove-Item -LiteralPath $c.Path -Recurse -Force -ErrorAction Stop
                }
            }
            "pattern-dir" {
                Get-ChildItem -Recurse -Directory -Force -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -eq "__pycache__" } |
                    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction Stop }
            }
            "pattern-file" {
                Get-ChildItem -Recurse -File -Force -Filter "*.pyc" -ErrorAction SilentlyContinue |
                    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop }
            }
        }
        Write-Host ("[OK] 已清理: {0}" -f $c.Name) -ForegroundColor Green
    } catch {
        Write-Host ("[ERR] 清理失败: {0} -> {1}" -f $c.Name, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "清理完成。建议你再跑一次体积统计确认结果。" -ForegroundColor Cyan
