# Stops and removes the scheduled tasks. Then close any leftover bot window or run: Get-Process python | Stop-Process
foreach ($n in "XAUUSD-Bot", "XAUUSD-NewsBrief", "XAUUSD-Portal", "XAUUSD-MT5") {
    Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Host "Removed."
