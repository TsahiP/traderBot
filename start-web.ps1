# Starts the API (dashboard.py) + the Next.js dashboard and opens the browser.
# -Paperbot also starts the live paper bot (paperbot.py, HTTP/SSE on :8100) for the Paper desk tab.
# Stop with Ctrl+C in each window, or close the spawned windows.

param([switch]$Paperbot)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "dashboard.py" -WorkingDirectory $root -WindowStyle Minimized
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "cd /d `"$root\web`" && npm run dev" -WindowStyle Minimized
if ($Paperbot) {
    Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "paperbot.py" -WorkingDirectory $root -WindowStyle Minimized
}

Start-Sleep -Seconds 6
Start-Process "http://localhost:3000"
Write-Host "TradeBot desk: http://localhost:3000  (API on :8000)"
if ($Paperbot) { Write-Host "paperbot: Paper desk tab, live on :8100 (logs\paperbot.log)" }
