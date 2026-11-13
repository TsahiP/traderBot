# Starts TradeBot stack: API, Next.js UI, and optional long-running bots.
# Default matches full desk: dashboard + web + paperbot + signalbot + fundabot + analystbot.
#
# Examples:
#   .\start-all.ps1
#   .\start-all.ps1 -NoPaperbot -NoAnalystbot
#   .\start-all.ps1 -Minimized
#
# Stop: Ctrl+C in each window, or close the spawned console windows.

param(
    [switch]$NoDashboard,
    [switch]$NoWeb,
    [switch]$NoPaperbot,
    [switch]$NoSignalbot,
    [switch]$NoFundabot,
    [switch]$NoAnalystbot,
    [switch]$NoBrowser,
    [switch]$NoHealthCheck,
    [switch]$Minimized
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Error "Missing $py — run: python -m venv .venv; .venv\Scripts\pip install -r requirements.txt"
}

$windowStyle = if ($Minimized) { "Minimized" } else { "Normal" }

function Start-TradeBotProcess {
    param(
        [string]$Title,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory = $root
    )
    $psi = @{
        FilePath         = $FilePath
        ArgumentList     = $ArgumentList
        WorkingDirectory = $WorkingDirectory
        WindowStyle      = $windowStyle
    }
    Start-Process @psi | Out-Null
    Write-Host "  started: $Title"
}

function Test-HttpOk {
    param(
        [string]$Url,
        [int]$TimeoutSec = 5
    )
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch {
        return $false
    }
}

function Wait-HttpOk {
    param(
        [string]$Label,
        [string]$Url,
        [int]$MaxWaitSec = 90,
        [int]$IntervalSec = 3
    )
    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk -Url $Url) {
            Write-Host "  OK  $Label — $Url"
            return $true
        }
        Start-Sleep -Seconds $IntervalSec
    }
    Write-Warning "  FAIL $Label — no response from $Url within ${MaxWaitSec}s"
    return $false
}

Write-Host ""
Write-Host "TradeBot start-all"
Write-Host "  root: $root"
Write-Host ""

if (-not $NoDashboard) {
    Start-TradeBotProcess -Title "dashboard.py (:8000)" -FilePath $py -ArgumentList @("dashboard.py")
}
if (-not $NoWeb) {
    Start-TradeBotProcess -Title "web dev (:3000)" -FilePath "cmd.exe" -ArgumentList @(
        "/k", "title TradeBot web && cd /d `"$root\web`" && npm run dev"
    )
}
if (-not $NoPaperbot) {
    Start-TradeBotProcess -Title "paperbot.py (:8100)" -FilePath $py -ArgumentList @("paperbot.py")
}
if (-not $NoSignalbot) {
    Start-TradeBotProcess -Title "signalbot.py" -FilePath $py -ArgumentList @("signalbot.py")
}
if (-not $NoFundabot) {
    Start-TradeBotProcess -Title "fundabot.py" -FilePath $py -ArgumentList @("fundabot.py")
}
if (-not $NoAnalystbot) {
    Start-TradeBotProcess -Title "analystbot.py" -FilePath $py -ArgumentList @("analystbot.py")
}

if (-not $NoHealthCheck) {
    Write-Host ""
    Write-Host "Health check (HTTP)..."
    $ok = $true
    if (-not $NoDashboard) {
        $ok = (Wait-HttpOk -Label "API" -Url "http://127.0.0.1:8000/api/strategies") -and $ok
    }
    if (-not $NoWeb) {
        $ok = (Wait-HttpOk -Label "Web UI" -Url "http://localhost:3000" -MaxWaitSec 120) -and $ok
    }
    if (-not $NoPaperbot) {
        $ok = (Wait-HttpOk -Label "Paperbot" -Url "http://127.0.0.1:8100/state" -MaxWaitSec 60) -and $ok
    }
    if (-not $ok) {
        Write-Warning "Some checks failed — see logs under logs\ and the spawned console windows."
    }
}

if (-not $NoBrowser -and -not $NoWeb) {
    Start-Sleep -Seconds 2
    Start-Process "http://localhost:3000"
}

Write-Host ""
Write-Host "Desk:    http://localhost:3000"
Write-Host "API:     http://127.0.0.1:8000"
if (-not $NoPaperbot) { Write-Host "Paper:   http://127.0.0.1:8100/state" }
Write-Host "Logs:    logs\dashboard.out, logs\web.out, logs\paperbot.log, logs\signalbot.log, logs\fundabot.log, logs\analystbot.log"
Write-Host ""
