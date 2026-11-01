# Writes today's high-impact USD news briefing to logs\news_YYYY-MM-DD.txt
param([string]$Repo = (Split-Path -Parent $PSScriptRoot))
Set-Location $Repo
New-Item -ItemType Directory -Force -Path "$Repo\logs" | Out-Null
$out = "$Repo\logs\news_" + (Get-Date -Format "yyyy-MM-dd") + ".txt"
python -m xauusd_agents newsbrief --out $out
