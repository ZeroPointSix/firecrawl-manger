param(
  [Parameter(Mandatory = $false)]
  [string]$BaseUrl = "http://127.0.0.1:8000",

  [Parameter(Mandatory = $false)]
  [int]$TimeoutSec = 10,

  [Parameter(Mandatory = $false)]
  [switch]$SkipReady
)

$ErrorActionPreference = "Stop"

function Normalize-BaseUrl([string]$Url) {
  $trimmed = ([string]$Url).Trim()
  if (-not $trimmed) {
    throw "BaseUrl is empty"
  }
  if ($trimmed.EndsWith("/")) {
    return $trimmed.TrimEnd("/")
  }
  return $trimmed
}

function Invoke-HttpJson([string]$Url, [int]$TimeoutSec) {
  $res = Invoke-WebRequest -Uri $Url -Method GET -UseBasicParsing -TimeoutSec $TimeoutSec
  $contentType = $res.Headers["Content-Type"]

  $json = $null
  if ($contentType -and $contentType.ToLowerInvariant().Contains("application/json")) {
    try {
      $json = $res.Content | ConvertFrom-Json -ErrorAction Stop
    } catch {
      $json = $null
    }
  }

  return [pscustomobject]@{
    StatusCode = [int]$res.StatusCode
    ContentType = $contentType
    Raw = $res.Content
    Json = $json
  }
}

function Write-Result([string]$Label, [bool]$Ok, [string]$Details) {
  $prefix = $(if ($Ok) { "[OK]" } else { "[FAIL]" })
  $color = $(if ($Ok) { "Green" } else { "Red" })
  Write-Host ("{0} {1}{2}" -f $prefix, $Label, $(if ($Details) { " - " + $Details } else { "" })) -ForegroundColor $color
}

$BaseUrl = Normalize-BaseUrl $BaseUrl
Write-Host "=== FCAM Smoke Test ===" -ForegroundColor Cyan
Write-Host ("BaseUrl: {0}" -f $BaseUrl) -ForegroundColor DarkGray
Write-Host ""

$failed = $false

try {
  $r = Invoke-HttpJson ("{0}/healthz" -f $BaseUrl) $TimeoutSec
  if ($r.StatusCode -ne 200) {
    $failed = $true
    Write-Result "GET /healthz" $false ("HTTP {0}" -f $r.StatusCode)
  } else {
    $details = $(if ($r.Json -and $null -ne $r.Json.ok) { "ok={0}" -f $r.Json.ok } else { "HTTP 200" })
    Write-Result "GET /healthz" $true $details
  }
} catch {
  $failed = $true
  Write-Result "GET /healthz" $false $_.Exception.Message
}

if (-not $SkipReady) {
  try {
    $r = Invoke-HttpJson ("{0}/readyz" -f $BaseUrl) $TimeoutSec
    if ($r.StatusCode -ne 200) {
      $failed = $true
      $details = "HTTP {0}" -f $r.StatusCode
      if ($r.Json -and $r.Json.message) {
        $details = "{0}; message={1}" -f $details, $r.Json.message
      }
      Write-Result "GET /readyz" $false $details
    } else {
      Write-Result "GET /readyz" $true "HTTP 200"
    }
  } catch {
    $failed = $true
    Write-Result "GET /readyz" $false $_.Exception.Message
  }
}

Write-Host ""
if ($failed) {
  Write-Host "Smoke test failed." -ForegroundColor Red
  exit 1
}

Write-Host "Smoke test passed." -ForegroundColor Green
exit 0

