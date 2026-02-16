param(
  [Parameter(Mandatory = $false)]
  [string]$BaseUrl = "http://127.0.0.1:8000",

  [Parameter(Mandatory = $false)]
  [int]$TimeoutSec = 10,

  [Parameter(Mandatory = $false)]
  [switch]$SkipReady
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot ".")).Path
$Smoke = Join-Path $RepoRoot "scripts\\smoke-fcam.ps1"

if (-not (Test-Path -LiteralPath $Smoke)) {
  throw ("Smoke script not found: {0}" -f $Smoke)
}

& $Smoke -BaseUrl $BaseUrl -TimeoutSec $TimeoutSec -SkipReady:$SkipReady
