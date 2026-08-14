# Starts the API (dashboard.py) + the Next.js dashboard and opens the browser.
# Stop both with Ctrl+C in each window, or close the two spawned windows.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "dashboard.py" -WorkingDirectory $root -WindowStyle Minimized
Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "cd /d `"$root\web`" && npm run dev" -WindowStyle Minimized

Start-Sleep -Seconds 6
Start-Process "http://localhost:3000"
Write-Host "TradeBot desk: http://localhost:3000  (API on :8000)"
