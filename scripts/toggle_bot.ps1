# Switch one committee bot on or off on THIS machine (remembered in bots.local.json, survives restarts and git pull).
#   powershell -ExecutionPolicy Bypass -File scripts\toggle_bot.ps1 -List
#   powershell -ExecutionPolicy Bypass -File scripts\toggle_bot.ps1 -Name GBPUSD -On
#   powershell -ExecutionPolicy Bypass -File scripts\toggle_bot.ps1 -Name GBPUSD -Off
# Switching OFF stops the bot; a position it already opened stays open in MT5 with its stop and target.
param([string]$Name, [switch]$On, [switch]$Off, [switch]$List)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$cfg = Get-Content (Join-Path $Root "bots.json") -Raw | ConvertFrom-Json
$localFile = Join-Path $Root "bots.local.json"
$local = [ordered]@{}
if (Test-Path $localFile) { (Get-Content $localFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $local[$_.Name] = [bool]$_.Value } }

function Show-List {
    foreach ($b in $cfg.bots) {
        $want = if ($local.Contains($b.name)) { $local[$b.name] } else { [bool]$b.enabled }
        $t = Get-ScheduledTask -TaskName "Svc-$($b.name)" -ErrorAction SilentlyContinue
        $state = if ($t) { $t.State } else { "not installed" }
        Write-Host ("{0,-8} {1,-4} task {2,-13} {3,-7} {4}" -f $b.name, $(if ($want) {"ON"} else {"off"}), $state, $b.symbol, $b.evidence)
    }
}

if ($List -or -not $Name) { Show-List; return }
if ($On -eq $Off) { throw "say -On or -Off" }
$bot = $cfg.bots | Where-Object { $_.name -ieq $Name }
if (-not $bot) { throw "unknown bot '$Name'. Known: $(($cfg.bots | ForEach-Object name) -join ', ')" }

$local[$bot.name] = [bool]$On
$local | ConvertTo-Json | Set-Content -Encoding UTF8 $localFile

$task = "Svc-$($bot.name)"
if (-not (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue)) {
    Write-Host "$task is not installed yet: saved your choice; run scripts\install_services.ps1 (or SETUP.bat) to create it."
    return
}
if ($On) {
    Enable-ScheduledTask -TaskName $task | Out-Null
    Start-ScheduledTask -TaskName $task
    Write-Host "$($bot.name) ON: started, and starts at every logon."
} else {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    Disable-ScheduledTask -TaskName $task | Out-Null
    Write-Host "$($bot.name) off: stopped, and will not start at logon. Open positions (magic $($bot.magic)) stay in MT5."
}
Show-List
