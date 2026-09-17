param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$startupPath = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupPath 'Channel Keeper.lnk'
if ($Remove) {
    if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath }
    Write-Output 'Login autostart removed. The running service is not stopped.'
    exit
}
$pythonPath = Join-Path $projectPath '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run start.cmd once before enabling autostart.' }
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonPath
$shortcut.Arguments = '"' + (Join-Path $projectPath 'run.py') + '" --no-browser'
$shortcut.WorkingDirectory = $projectPath
$shortcut.WindowStyle = 7
$shortcut.Description = 'Channel Keeper local background service (127.0.0.1:8765)'
$shortcut.Save()
Write-Output 'Login autostart enabled. Open http://127.0.0.1:8765 after your next login.'
