# Switch, add or list committee bots on THIS machine (choices kept in bots.local.json: survive restarts and git pull).
#   powershell -ExecutionPolicy Bypass -File scripts\toggle_bot.ps1 -List
#   powershell -ExecutionPolicy Bypass -File scripts\toggle_bot.ps1 -Name GBPUSD -On     (or -Off)
#   powershell -ExecutionPolicy Bypass -File scripts\toggle_bot.ps1 -Add -Symbol EURJPY -SlAtr 1.5 -TpAtr 2.0
# The portal's Instruments panel calls this same script. Switching OFF stops the bot; a position it already opened
# stays open in MT5 with its stop and target.
param([string]$Name, [switch]$On, [switch]$Off, [switch]$List, [switch]$Json,
      [switch]$Add, [string]$Symbol, [double]$SlAtr = 1.5, [double]$TpAtr = 2.0)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "svc_common.ps1")
$local = Read-Local

function Get-View {
    $cfg = Get-Bots $local
    foreach ($b in $cfg.bots) {
        $t = Get-ScheduledTask -TaskName "Svc-$($b.name)" -ErrorAction SilentlyContinue
        [pscustomobject]@{ name = $b.name; symbol = $b.symbol; on = [bool]$b.on; sl_atr = $b.sl_atr; tp_atr = $b.tp_atr; magic = $b.magic
                           logdir = $b.logdir; evidence = $b.evidence; added = [bool]($local.extra | Where-Object { $_.name -eq $b.name })
                           task = $(if ($t) { [string]$t.State } else { "not installed" }) }
    }
}
function Show-View {
    $v = @(Get-View)
    if ($Json) { ConvertTo-Json @{ bots = $v; live = $local.live; installed = [bool]$local.python } -Depth 4 -Compress; return }
    $v | ForEach-Object { Write-Host ("{0,-8} {1,-4} task {2,-13} {3,-7} {4}" -f $_.name, $(if ($_.on) {"ON"} else {"off"}), $_.task, $_.symbol, $_.evidence) }
}
function Apply($bot) {
    if (-not $local.python) { Write-Host "Saved. Not installed on this machine yet: run SETUP.bat or scripts\install_services.ps1."; return }
    $cfg = Get-Bots $local
    $b = $cfg.bots | Where-Object { $_.name -eq $bot }
    if (Get-ScheduledTask -TaskName "Svc-$bot" -ErrorAction SilentlyContinue) { Set-SvcState $bot $b.on }
    else { Install-Bot $b $cfg $local.python $local.live }
}

if ($Add) {
    $sym = $Symbol.Trim().ToUpper()
    if ($sym -notmatch '^[A-Z0-9._#]{2,20}$') { throw "invalid symbol '$Symbol'" }
    if ($SlAtr -le 0 -or $SlAtr -gt 10 -or $TpAtr -le 0 -or $TpAtr -gt 20) { throw "stop/target ATR out of range" }
    $cfg = Get-Bots $local
    if ($cfg.bots | Where-Object { $_.symbol -ieq $sym }) { throw "$sym is already in the list: switch it on instead" }
    $name = ($sym -replace '[^A-Z0-9]', '')
    $magic = 1 + ($cfg.bots | ForEach-Object { [int]$_.magic } | Measure-Object -Maximum).Maximum
    $local.extra = @($local.extra) + [pscustomobject]@{ name = $name; symbol = $sym; sl_atr = $SlAtr; tp_atr = $TpAtr; magic = $magic; logdir = "logs_$($name.ToLower())" }
    $local.enabled[$name] = $true
    Save-Local $local
    Apply $name
    if (-not $Json) { Write-Host "Added $sym (magic $magic) and switched it ON." }
    Show-View; return
}

if ($List -or -not $Name) { Show-View; return }
if ($On -eq $Off) { throw "say -On or -Off" }
$bot = (Get-Bots $local).bots | Where-Object { $_.name -ieq $Name }
if (-not $bot) { throw "unknown bot '$Name'" }
$local.enabled[$bot.name] = [bool]$On
Save-Local $local
Apply $bot.name
if (-not $Json) { Write-Host "$($bot.name) $(if ($On) {'ON'} else {'off'})." }
Show-View
