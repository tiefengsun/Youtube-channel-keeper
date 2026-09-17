@echo off
setlocal
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/shutdown' -Method Post -Headers @{'X-Local-Request'='1'} -ErrorAction Stop | Out-Null; Write-Host 'Stopping Channel Keeper. Unfinished downloads will resume next time.' } catch { Write-Host 'Cannot reach Channel Keeper. If it is running on another port, press Ctrl+C in its service window.' }"
pause
