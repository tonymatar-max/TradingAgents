# ONE setup file for a Windows server: everything the bots need, then everything started at logon.
#   Installs Python 3.12 + Git (if missing), gets/updates the bot code (branch below), installs packages,
#   checks MetaTrader 5, removes the old Bot-* tasks, then runs scripts\install_services.ps1 which registers:
#     Svc-MT5     MetaTrader 5 at logon
#     Svc-USTEC   USTEC committee bot    (demo orders with -LiveOrders, else dry run)
#     Svc-GoldV2  gold v2 committee bot  (demo orders with -LiveOrders, else dry run)
#     Svc-ORB     ORB bot                (ALWAYS dry run)
#     Svc-Portal  agents portal          http://127.0.0.1:8780 on this server
#
#   Double-click SETUP.bat (dry run)  or  run:  .\SETUP.bat live   (demo orders)
#   Safe to run again: it updates the code and re-registers the tasks.
#
# Before running: MetaTrader 5 installed, logged in to your Exness DEMO account, Algo Trading switched on.
param(
    [string]$InstallDir = "C:	radingagents",
    [string]$RepoUrl    = "https://github.com/tonymatar-max/TradingAgents.git",
    [string]$Branch     = "feat/ai-panel-claude-grok",
    [switch]$LiveOrders,
    [switch]$NoTasks     # only install; don't register startup tasks
)
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

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
if (-not (Test-Path (Join-Path $InstallDir ".git"))) {
    git clone $RepoUrl $InstallDir   # private repo: Git asks you to sign in to GitHub
    if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
}
git -C $InstallDir fetch origin
git -C $InstallDir checkout $Branch
if ($LASTEXITCODE -ne 0) { throw "could not switch to branch $Branch (local changes in $InstallDir?)" }
git -C $InstallDir pull --ff-only origin $Branch
git -C $InstallDir log --oneline -1

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

# ---------------------------------------------------------------- 6. Old tasks
Step "Remove old Bot-* tasks (replaced by Svc-*)"
Get-ScheduledTask -TaskName "Bot-*" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-ScheduledTask -TaskName $_.TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
    Write-Host "Removed $($_.TaskName)"
}
Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'xauusd_agents mt5|orb_bot.py|agents_portal' } |
    ForEach-Object { Write-Host "Stopping old bot process $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# ---------------------------------------------------------------- 7. Startup tasks
if ($NoTasks) {
    Write-Host "NoTasks: code and packages installed; nothing registered."
} else {
    Step "Startup tasks (Svc-*)"
    $svc = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $InstallDir "scripts\install_services.ps1"), "-Python", $pyExe)
    if (-not $LiveOrders) { $svc += "-DryRun" }
    & powershell.exe @svc
    if ($LASTEXITCODE -ne 0) { throw "install_services.ps1 failed" }
    Start-Sleep -Seconds 5
    Get-ScheduledTask -TaskName "Svc-*" | Select-Object TaskName, State | Format-Table -AutoSize
}

Step "Done"
Write-Host @"
Portal:  http://127.0.0.1:8780 (on this server)
Status:  Get-ScheduledTask Svc-* | Select-Object TaskName,State
Stop:    Get-ScheduledTask Svc-* | Stop-ScheduledTask
Remove:  powershell -ExecutionPolicy Bypass -File $InstallDir\scripts\install_services.ps1 -Uninstall
IMPORTANT: if this server and your PC use the SAME demo account, run the bots on only ONE of them,
or every trade is placed twice.
Set Windows to sign in automatically after a reboot; close Remote Desktop instead of signing out.
"@
