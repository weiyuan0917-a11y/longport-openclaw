param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status",
    [switch]$FrontendOnly,
    [switch]$BackendOnly
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Root ".runtime\silent"
$LogDir = Join-Path $Root "logs\silent"
$FrontendPidFile = Join-Path $RuntimeDir "frontend.pid"
$BackendPidFile = Join-Path $RuntimeDir "backend.pid"
$FrontendOutLog = Join-Path $LogDir "frontend.out.log"
$FrontendErrLog = Join-Path $LogDir "frontend.err.log"
$BackendOutLog = Join-Path $LogDir "backend.out.log"
$BackendErrLog = Join-Path $LogDir "backend.err.log"

$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
$FrontendDir = Join-Path $Root "frontend"
$BackendDir = $Root

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PidFromFile([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return $null }
    $raw = (Get-Content -Path $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $pidValue = 0
    if ([int]::TryParse([string]$raw, [ref]$pidValue)) { return $pidValue }
    return $null
}

function Test-PidAlive([int]$PidValue) {
    if (-not $PidValue) { return $false }
    try {
        $p = Get-Process -Id $PidValue -ErrorAction Stop
        return $null -ne $p
    } catch {
        return $false
    }
}

function Stop-Managed([string]$Name, [string]$PidFile) {
    $pidValue = Get-PidFromFile $PidFile
    if (-not $pidValue) {
        Write-Output "${Name}: not running (no pid file)"
        return
    }
    if (-not (Test-PidAlive $pidValue)) {
        Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
        Write-Output "${Name}: not running (stale pid file cleaned)"
        return
    }
    Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
    if (Test-PidAlive $pidValue) {
        Write-Output "${Name}: failed to stop pid=$pidValue"
        return
    }
    Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
    Write-Output "${Name}: stopped pid=$pidValue"
}

function Start-Managed(
    [string]$Name,
    [string]$PidFile,
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory,
    [string]$OutLog,
    [string]$ErrLog
) {
    $existingPid = Get-PidFromFile $PidFile
    if ($existingPid -and (Test-PidAlive $existingPid)) {
        Write-Output "${Name}: already running pid=$existingPid"
        return
    }
    if ($existingPid) {
        Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
    }

    $proc = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -WindowStyle Hidden `
        -PassThru

    Set-Content -Path $PidFile -Value ([string]$proc.Id) -Encoding ASCII
    Write-Output "${Name}: started pid=$($proc.Id)"
}

function Check-Http([string]$Url) {
    try {
        $res = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return "ok($($res.StatusCode))"
    } catch {
        return "down"
    }
}

function Get-ListeningPids([int]$Port) {
    $rows = netstat -ano | Select-String ":$Port" | Select-String "LISTENING"
    $out = @()
    foreach ($line in $rows) {
        $parts = ($line.ToString().Trim() -split "\s+")
        if ($parts.Length -lt 5) { continue }
        $pidText = $parts[$parts.Length - 1]
        $pidValue = 0
        if ([int]::TryParse($pidText, [ref]$pidValue) -and $pidValue -gt 0) {
            if (-not ($out -contains $pidValue)) {
                $out += $pidValue
            }
        }
    }
    return $out
}

function Cleanup-BackendPort {
    $managedPid = Get-PidFromFile $BackendPidFile
    if ($managedPid -and -not (Test-PidAlive $managedPid)) {
        $managedPid = $null
        Remove-Item -Path $BackendPidFile -Force -ErrorAction SilentlyContinue
    }
    $listenPids = Get-ListeningPids 8000
    foreach ($pidValue in $listenPids) {
        if ($managedPid -and $pidValue -eq $managedPid) { continue }
        try {
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
            Write-Output "backend-port-cleanup: killed pid=$pidValue on :8000"
        } catch {}
    }
}

function Cleanup-AutoTraderAndFeishuDuplicates {
    $targetScriptKeywords = @(
        "api\auto_trader_supervisor.py",
        "api\auto_trader_worker.py",
        "mcp_server\feishu_command_bot.py"
    )
    $rootNorm = $Root.ToLowerInvariant()
    $venvNorm = $PythonExe.ToLowerInvariant()
    $targets = @()
    try {
        $targets = Get-CimInstance Win32_Process | Where-Object {
            if ($_.Name -ne "python.exe") { return $false }
            $cmd = [string]$_.CommandLine
            if ([string]::IsNullOrWhiteSpace($cmd)) { return $false }
            $cmdNorm = $cmd.ToLowerInvariant()
            if (-not $cmdNorm.Contains($rootNorm)) { return $false }
            if (-not $cmdNorm.Contains($venvNorm)) { return $false }
            foreach ($k in $targetScriptKeywords) {
                if ($cmdNorm.Contains($k.ToLowerInvariant())) { return $true }
            }
            return $false
        }
    } catch {
        $targets = @()
    }
    foreach ($proc in @($targets)) {
        $pidValue = 0
        if (-not [int]::TryParse([string]$proc.ProcessId, [ref]$pidValue)) { continue }
        if ($pidValue -le 0) { continue }
        try {
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
            Write-Output "prestart-cleanup: killed pid=$pidValue ($($proc.Name))"
        } catch {}
    }
}

$manageFrontend = $true
$manageBackend = $true
if ($FrontendOnly) { $manageBackend = $false }
if ($BackendOnly) { $manageFrontend = $false }

switch ($Action) {
    "start" {
        Cleanup-AutoTraderAndFeishuDuplicates
        if ($manageBackend) {
            Cleanup-BackendPort
            if (-not (Test-Path $PythonExe)) {
                throw "Missing python: $PythonExe"
            }
            $runApi = Join-Path $Root "scripts\run_api.py"
            $uvJson = & $PythonExe $runApi --print-argv-json --port 8000
            $uvArgs = $uvJson | ConvertFrom-Json
            Start-Managed `
                -Name "backend" `
                -PidFile $BackendPidFile `
                -FilePath $PythonExe `
                -Arguments $uvArgs `
                -WorkingDirectory $BackendDir `
                -OutLog $BackendOutLog `
                -ErrLog $BackendErrLog
        }
        if ($manageFrontend) {
            Start-Managed `
                -Name "frontend" `
                -PidFile $FrontendPidFile `
                -FilePath "cmd.exe" `
                -Arguments @("/c", "npm run dev -- --hostname 127.0.0.1 --port 3000") `
                -WorkingDirectory $FrontendDir `
                -OutLog $FrontendOutLog `
                -ErrLog $FrontendErrLog
        }
        Start-Sleep -Seconds 1
        Write-Output "frontend_url: http://127.0.0.1:3000"
        Write-Output "backend_url: http://127.0.0.1:8000/health"
        Write-Output "log_dir: $LogDir"
    }
    "stop" {
        if ($manageFrontend) { Stop-Managed -Name "frontend" -PidFile $FrontendPidFile }
        if ($manageBackend) { Stop-Managed -Name "backend" -PidFile $BackendPidFile }
    }
    "status" {
        if ($manageFrontend) {
            $fpid = Get-PidFromFile $FrontendPidFile
            $frun = if ($fpid -and (Test-PidAlive $fpid)) { "running(pid=$fpid)" } else { "stopped" }
            $fhttp = Check-Http "http://127.0.0.1:3000/auto-trader"
            Write-Output "frontend: $frun http=$fhttp"
        }
        if ($manageBackend) {
            $bpid = Get-PidFromFile $BackendPidFile
            $brun = if ($bpid -and (Test-PidAlive $bpid)) { "running(pid=$bpid)" } else { "stopped" }
            $bhttp = Check-Http "http://127.0.0.1:8000/health"
            Write-Output "backend: $brun http=$bhttp"
        }
        Write-Output "log_dir: $LogDir"
    }
}
