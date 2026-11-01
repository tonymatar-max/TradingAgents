# Runs the XAUUSD bot forever and restarts it 30 s after any exit or crash.
# DRY RUN unless -LiveOrders is passed (demo accounts only; the bot refuses real accounts).
param(
    [string]$Repo = (Split-Path -Parent $PSScriptRoot),
    [switch]$LiveOrders,
    [string]$Magic = "26004"
)
Set-Location $Repo
New-Item -ItemType Directory -Force -Path "$Repo\logs" | Out-Null

$botArgs = @("mt5", "--symbol", "XAUUSD", "--timeframe", "H1",
             "--threshold", "0.8", "--sl-atr", "2.0", "--tp-atr", "2.0", "--htf-min", "6",
             "--news", "--magic", $Magic, "--log-file", "$Repo\logs\bot.log")
if ($LiveOrders) { $botArgs += "--live-orders" }

while ($true) {
    python -m xauusd_agents @botArgs 2>> "$Repo\logs\crash.log"
    Add-Content "$Repo\logs\crash.log" ("{0}  bot exited, restarting in 30 s" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    Start-Sleep -Seconds 30
}
