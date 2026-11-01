# Registers Windows scheduled tasks (run in a normal PowerShell window; no admin needed):
#   XAUUSD-Bot       starts the bot at logon (restarts if it exits)
#   XAUUSD-NewsBrief writes the daily news briefing
#   XAUUSD-Portal    serves the results page at http://127.0.0.1:8765
#   XAUUSD-MT5       (optional) starts the MT5 terminal at logon if you pass -Mt5Path
# Dry run by default. Pass -LiveOrders only when you want the demo account to trade.
param(
    [string]$Repo = (Split-Path -Parent $PSScriptRoot),
    [switch]$LiveOrders,
    [string]$Mt5Path = "",
    [string]$NewsTime = "06:30"
)
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1)

# 1) the bot
$botArg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$Repo\scripts\run_bot.ps1`""
if ($LiveOrders) { $botArg += " -LiveOrders" }
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = "PT1M"      # give MT5 a minute to start first
Register-ScheduledTask -TaskName "XAUUSD-Bot" -Principal $principal -Settings $settings -Trigger $trigger `
    -Action (New-ScheduledTaskAction -Execute "powershell.exe" -Argument $botArg -WorkingDirectory $Repo) -Force | Out-Null

# 2) daily news briefing
$newsArg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Repo\scripts\news_brief.ps1`""
Register-ScheduledTask -TaskName "XAUUSD-NewsBrief" -Principal $principal -Settings $settings `
    -Trigger (New-ScheduledTaskTrigger -Daily -At $NewsTime) `
    -Action (New-ScheduledTaskAction -Execute "powershell.exe" -Argument $newsArg -WorkingDirectory $Repo) -Force | Out-Null

# 2b) the portal (read-only web page on this PC only: http://127.0.0.1:8765)
Register-ScheduledTask -TaskName "XAUUSD-Portal" -Principal $principal -Settings $settings `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $user) `
    -Action (New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $Repo `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"python -m xauusd_agents portal --logdir logs`"") -Force | Out-Null

# 3) optional: start MT5 at logon
if ($Mt5Path -ne "") {
    Register-ScheduledTask -TaskName "XAUUSD-MT5" -Principal $principal -Settings $settings `
        -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $user) `
        -Action (New-ScheduledTaskAction -Execute $Mt5Path) -Force | Out-Null
}

Write-Host "Installed. Mode: $(if ($LiveOrders) { 'LIVE ORDERS on the demo account' } else { 'DRY RUN (no orders)' })"
Write-Host "Start now without rebooting:  Start-ScheduledTask -TaskName XAUUSD-Bot"
Write-Host "Logs: $Repo\logs\bot.log   crash.log   news_YYYY-MM-DD.txt"
