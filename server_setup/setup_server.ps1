# One-file setup for the committee trading bots on a Windows server.
# Installs Python 3.12 + Git (if missing), clones the bot repo, installs packages,
# creates one launcher per bot, and registers them to start at logon.
#
# Run in PowerShell (normal user, no admin needed except maybe for Git):
#   powershell -ExecutionPolicy Bypass -File setup_server.ps1                  # DRY RUN bots (no orders)
#   powershell -ExecutionPolicy Bypass -File setup_server.ps1 -LiveOrders      # bots send orders (DEMO accounts only)
#
# Before running: MetaTrader 5 installed, logged in to your Exness account,
# Tools > Options > Expert Advisors > "Allow algorithmic trading" ticked.
param(
    [string]$InstallDir = "C:\tradingagents",
    [string]$RepoUrl    = "https://github.com/tonymatar-max/tradingagents.git",
    [switch]$LiveOrders,
    [switch]$NoTasks     # only install; don't register startup tasks
)
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

# Bots to run: name, symbol, threshold, sl_atr, tp_atr, risk, magic  (the settings tested in backtests)
$Bots = @(
    @{ Name = "USTEC";   Symbol = "USTEC";  Thr = "0.9"; Sl = "1.5"; Tp = "2.0"; Risk = "0.01"; Magic = "26005" },
    @{ Name = "GoldV2";  Symbol = "XAUUSD"; Thr = "0.9"; Sl = "1.0"; Tp = "3.0"; Risk = "0.01"; Magic = "26006" }
)

# ---------------------------------------------------------------- 1. Python 3.12
Step "Python 3.12"
$py = $null
if (Have "py") { try { & py -3.12 --version *> $null; if ($LASTEXITCODE -eq 0) { $py = "py -3.12" } } catch {} }
if (-not $py) {
    $exe = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
    Write-Host "Downloading Python 3.12.10 from python.org ..."
    Invoke-WebRequest "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" -OutFile $exe -UseBasicParsing
    Write-Host "Installing (current user, added to PATH) ..."
    Start-Process $exe -Wait -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0"
    Refresh-Path
    & py -3.12 --version
    if ($LASTEXITCODE -ne 0) { throw "Python install failed. Install it manually from the link above, then rerun." }
    $py = "py -3.12"
}
$pyExe = (& py -3.12 -c "import sys; print(sys.executable)").Trim()
Write-Host "Using $pyExe"

# ---------------------------------------------------------------- 2. Git
Step "Git"
if (-not (Have "git")) {
    if (Have "winget") {
        winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements --silent
    } else {
        $g = Join-Path $env:TEMP "git-setup.exe"
        Invoke-WebRequest "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe" -OutFile $g -UseBasicParsing
        Start-Process $g -Wait -ArgumentList "/VERYSILENT /NORESTART"
    }
    Refresh-Path
    if (-not (Have "git")) { $env:Path += ";C:\Program Files\Git\cmd" }
}
git --version

# ---------------------------------------------------------------- 3. Code
Step "Bot code -> $InstallDir"
if (Test-Path (Join-Path $InstallDir ".git")) {
    git -C $InstallDir pull --ff-only
} else {
    git clone $RepoUrl $InstallDir   # private repo: Git asks you to sign in to GitHub
}

# ---------------------------------------------------------------- 4. Packages
Step "Python packages"
& $pyExe -m pip install --upgrade pip --quiet
& $pyExe -m pip install -r (Join-Path $InstallDir "requirements.txt") MetaTrader5 --quiet
& $pyExe -c "import MetaTrader5, pandas, numpy; print('packages OK')"

# ---------------------------------------------------------------- 5. MT5 check
Step "MetaTrader 5 check"
$mt5 = @("$env:ProgramFiles\MetaTrader 5 EXNESS\terminal64.exe", "$env:ProgramFiles\MetaTrader 5\terminal64.exe") +
       (Get-ChildItem "$env:ProgramFiles" -Filter terminal64.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue | ForEach-Object FullName) |
       Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($mt5) { Write-Host "Found MT5: $mt5" } else { Write-Warning "terminal64.exe not found; MT5 won't auto-start (start it yourself)." }
& $pyExe -c @"
import MetaTrader5 as m
if not m.initialize(): print('MT5 not reachable:', m.last_error(), '- open MT5 and log in, then rerun')
else:
    a = m.account_info(); print('MT5 OK:', a.server, a.login, 'DEMO' if a.trade_mode == 0 else 'REAL', a.balance, a.currency)
    for s in ('USTEC', 'XAUUSD'): print(' ', s, 'found' if m.symbol_select(s, True) else 'NOT FOUND - fix the symbol name in setup_server.ps1')
    m.shutdown()
"@

# ---------------------------------------------------------------- 6. Launchers
Step "Bot launchers"
$scripts = Join-Path $InstallDir "scripts"
New-Item -ItemType Directory -Force $scripts | Out-Null
foreach ($b in $Bots) {
    $log = Join-Path $InstallDir "logs_$($b.Name.ToLower())"
    $live = if ($LiveOrders) { " --live-orders" } else { "" }
    $file = Join-Path $scripts "run_$($b.Name.ToLower()).ps1"
    @"
# Auto-generated by setup_server.ps1 - runs the $($b.Name) bot forever, restarting 30 s after any exit.
Set-Location "$InstallDir"
New-Item -ItemType Directory -Force "$log" | Out-Null
while (`$true) {
    & "$pyExe" -m xauusd_agents mt5 --symbol $($b.Symbol) --timeframe H1 --threshold $($b.Thr) --sl-atr $($b.Sl) --tp-atr $($b.Tp) --risk $($b.Risk) --magic $($b.Magic) --logdir "$log" --log-file "$log\bot.log"$live 2>> "$log\crash.log"
    Add-Content "$log\crash.log" ("{0}  bot exited, restarting in 30 s" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
    Start-Sleep -Seconds 30
}
"@ | Set-Content -Encoding UTF8 $file
    Write-Host "Created $file  ($(if ($LiveOrders) {'LIVE orders on demo'} else {'DRY RUN'}))"
}

# ---------------------------------------------------------------- 7. Startup tasks
if (-not $NoTasks) {
    Step "Startup tasks"
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1)
    if ($mt5) {
        Register-ScheduledTask -TaskName "Bot-MT5" -Principal $principal -Settings $settings -Force `
            -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $user) -Action (New-ScheduledTaskAction -Execute $mt5) | Out-Null
        Write-Host "Task Bot-MT5: starts MetaTrader 5 at logon"
    }
    foreach ($b in $Bots) {
        $file = Join-Path $scripts "run_$($b.Name.ToLower()).ps1"
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $trigger.Delay = "PT1M"   # give MT5 a minute to start
        Register-ScheduledTask -TaskName "Bot-$($b.Name)" -Principal $principal -Settings $settings -Trigger $trigger -Force `
            -Action (New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $InstallDir `
                -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$file`"") | Out-Null
        Start-ScheduledTask -TaskName "Bot-$($b.Name)"
        Write-Host "Task Bot-$($b.Name): started now and at every logon"
    }
}

Step "Done"
Write-Host @"
Bots log to $InstallDir\logs_ustec\bot.log and logs_goldv2\bot.log
Stop a bot:   Stop-ScheduledTask Bot-USTEC   (and Disable-ScheduledTask to keep it off)
Remove all:   Unregister-ScheduledTask Bot-* -Confirm:`$false
IMPORTANT: stop the same bots on your other PC, or every trade is placed twice on the same account.
Set Windows to log in automatically after reboot, and close (don't log off) Remote Desktop.
"@
