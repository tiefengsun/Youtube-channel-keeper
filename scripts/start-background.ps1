param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [string]$DataDir = '',
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv\Scripts\pythonw.exe'
$runPath = Join-Path $projectPath 'run.py'
$url = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Python environment is missing. Run start.cmd to create it.'
}

function Get-ChannelKeeperHealth {
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 1 -ErrorAction Stop
        if ($health.app_id -eq 'channel-keeper') { return $health }
    } catch {
        return $null
    }
    return $null
}

if (Get-ChannelKeeperHealth) {
    if (-not $NoBrowser) { Start-Process $url }
    Write-Output "Channel Keeper is already running: $url"
    exit 0
}

$arguments = @("`"$runPath`"", '--no-browser', '--port', $Port)
if ($DataDir) {
    if ([System.IO.Path]::IsPathRooted($DataDir)) {
        $resolvedDataDir = [System.IO.Path]::GetFullPath($DataDir)
    } else {
        $resolvedDataDir = [System.IO.Path]::GetFullPath((Join-Path $projectPath $DataDir))
    }
    $arguments += @('--data-dir', "`"$resolvedDataDir`"")
}

$serviceProcess = Start-Process -FilePath $pythonPath `
    -ArgumentList $arguments `
    -WorkingDirectory $projectPath `
    -WindowStyle Hidden `
    -PassThru

for ($attempt = 0; $attempt -lt 120; $attempt++) {
    Start-Sleep -Milliseconds 250
    if (Get-ChannelKeeperHealth) {
        if (-not $NoBrowser) { Start-Process $url }
        Write-Output "Channel Keeper started in the background: $url"
        exit 0
    }
    if ($serviceProcess.HasExited) {
        throw "Channel Keeper exited during startup. Check data\keeper.log and data\background.log."
    }
}

if (-not $serviceProcess.HasExited) {
    Stop-Process -Id $serviceProcess.Id -Force -ErrorAction SilentlyContinue
}
throw "Channel Keeper did not become ready within 30 seconds. Check data\keeper.log and data\background.log."
