# Runs everything "like a service": MetaTrader 5, every committee bot (on or off as chosen), and the agents portal.
#
# Why scheduled tasks and not Windows services: the MetaTrader5 Python package can only talk to an MT5 terminal running
# on the logged-in desktop. A real Windows service runs in a hidden session (session 0) where that does not work.
# These tasks start at logon, restart any program that exits (30 s), and run minimized.
#
#   Install:    powershell -ExecutionPolicy Bypass -File scripts\install_services.ps1
#   Dry run:    ... -File scripts\install_services.ps1 -DryRun        (bots log signals, place no orders)
#   On / off:   the portal's Instruments panel, or scripts\toggle_bot.ps1 -Name GBPUSD -Off   (or -On; -List)
#   Status:     Get-ScheduledTask Svc-* | Select TaskName,State
#   Remove all: powershell -ExecutionPolicy Bypass -File scripts\install_services.ps1 -Uninstall
#
# DEMO only: the bots refuse a non-demo account. Never run the same bot (same magic) on two machines on one account.
param(
    [switch]$DryRun,
    [switch]$Uninstall,
    [int]$PortalPort = 8780,
    [string]$Python = ""     # full path to python.exe; default = the "python" on PATH
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "svc_common.ps1")

if ($Uninstall) {
    Get-ScheduledTask -TaskName "Svc-*" -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-ScheduledTask -TaskName $_.TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
        Write-Host "Removed $($_.TaskName)"
    }
    return
}

$py = if ($Python) { $Python } else { (Get-Command python -ErrorAction Stop).Source }
& $py -c "import MetaTrader5, pandas, numpy" 2>$null
if ($LASTEXITCODE -ne 0) { throw "python ($py) is missing MetaTrader5/pandas/numpy. Run: python -m pip install -r requirements.txt MetaTrader5" }

$local = Read-Local
$local.live = -not $DryRun
$local.python = $py
Save-Local $local                 # the portal's on/off and add buttons reuse this mode and python
$cfg = Get-Bots $local

# the ORB bot is retired: remove its task if an older install created it
if (Get-ScheduledTask -TaskName "Svc-ORB" -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName "Svc-ORB" -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName "Svc-ORB" -Confirm:$false
    Write-Host "Removed Svc-ORB (ORB bot retired)"
}

$mt5 = @("$env:ProgramFiles\MetaTrader 5 EXNESS\terminal64.exe", "$env:ProgramFiles\MetaTrader 5\terminal64.exe") +
       (Get-ChildItem "$env:ProgramFiles" -Filter terminal64.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue | ForEach-Object FullName) |
       Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($mt5) {
    $user = "$env:USERDOMAIN\$env:USERNAME"
    Register-ScheduledTask -TaskName "Svc-MT5" -Force -Principal (New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive) `
        -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)) `
        -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $user) -Action (New-ScheduledTaskAction -Execute $mt5) | Out-Null
    Write-Host "Svc-MT5      : starts MetaTrader 5 at logon ($mt5)"
} else {
    Write-Warning "terminal64.exe not found: start MT5 yourself after each logon."
}

foreach ($b in $cfg.bots) {
    Install-Bot $b $cfg $py $local.live
    Write-Host ("Svc-{0,-8}: {1,-3}  {2} 9/10, stop {3} ATR, target {4} ATR, magic {5}   ({6})" -f $b.name, $(if ($b.on) {"ON"} else {"off"}), $b.symbol, $b.sl_atr, $b.tp_atr, $b.magic, $b.evidence)
}

$portalLog = Join-Path $Root "logs_portal"
Register-Svc "Portal" (Write-Launcher "Portal" $portalLog "& `"$py`" agents_portal\server.py --port $PortalPort --root `"$Root`"") $true $false
Write-Host "Svc-Portal  : ON   http://127.0.0.1:$PortalPort"

$n = @($cfg.bots | Where-Object on).Count
Write-Host @"

Mode: $(if ($DryRun) { 'DRY RUN (no orders)' } else { 'LIVE ORDERS on the DEMO account' }). $n bots ON, each risking $([int]($cfg.risk * 100))% per trade:
up to $n positions can be open at once (about $($n * [int]($cfg.risk * 100))% of the balance at risk in total).
Switch or add instruments in the portal (Instruments button), or: scripts\toggle_bot.ps1 -List
For restarts without you: set Windows to sign in automatically, and lock the screen instead of logging off.
"@
