param(
  [Parameter(Mandatory = $false)]
  [string]$BaseUrl = "http://127.0.0.1:8000",

  [Parameter(Mandatory = $false)]
  [int]$TimeoutSec = 10,

  [Parameter(Mandatory = $false)]
  [switch]$SkipReady,

  [Parameter(Mandatory = $false)]
  [string]$AdminToken = "",

  [Parameter(Mandatory = $false)]
  [string]$ClientToken = "",

  [Parameter(Mandatory = $false)]
  [string]$ScrapeUrl = "https://example.com"
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

function Invoke-HttpJson(
  [string]$Url,
  [string]$Method,
  [int]$TimeoutSec,
  [hashtable]$Headers = $null,
  [string]$Body = $null,
  [string]$ContentType = $null
) {
  $statusCode = $null
  $raw = $null
  $contentType = $null
  $error = $null

  try {
    $params = @{
      Uri = $Url
      Method = $Method
      UseBasicParsing = $true
      TimeoutSec = $TimeoutSec
    }
    if ($Headers) {
      $params["Headers"] = $Headers
    }
    if ($Body) {
      $params["Body"] = $Body
    }
    if ($ContentType) {
      $params["ContentType"] = $ContentType
    }

    $res = Invoke-WebRequest @params
    $statusCode = [int]$res.StatusCode
    $raw = $res.Content
    $contentType = $res.Headers["Content-Type"]
  } catch {
    $error = $_.Exception.Message
    $resp = $_.Exception.Response
    if ($resp) {
      try {
        $statusCode = [int]$resp.StatusCode.value__
      } catch {
        $statusCode = $null
      }
      try {
        $contentType = $resp.Headers["Content-Type"]
      } catch {
        try {
          $contentType = $resp.ContentType
        } catch {
          $contentType = $null
        }
      }
      try {
        $stream = $resp.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        $raw = $reader.ReadToEnd()
        $reader.Close()
      } catch {
        $raw = $null
      }
    }
  }

  $json = $null
  if ($contentType -and $contentType.ToLowerInvariant().Contains("application/json") -and $raw) {
    try {
      $json = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
      $json = $null
    }
  }

  return [pscustomobject]@{
    StatusCode = $statusCode
    ContentType = $contentType
    Raw = $raw
    Json = $json
    Error = $error
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
  $r = Invoke-HttpJson ("{0}/healthz" -f $BaseUrl) "GET" $TimeoutSec
  if ($r.StatusCode -ne 200) {
    $failed = $true
    $details = $(if ($null -ne $r.StatusCode) { "HTTP {0}" -f $r.StatusCode } else { $r.Error })
    Write-Result "GET /healthz" $false $details
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
    $r = Invoke-HttpJson ("{0}/readyz" -f $BaseUrl) "GET" $TimeoutSec
    if ($r.StatusCode -ne 200) {
      $failed = $true
      $details = $(if ($null -ne $r.StatusCode) { "HTTP {0}" -f $r.StatusCode } else { $r.Error })
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

if ($AdminToken) {
  try {
    $headers = @{ Authorization = "Bearer $AdminToken" }
    $r = Invoke-HttpJson ("{0}/admin/stats" -f $BaseUrl) "GET" $TimeoutSec $headers
    if ($r.StatusCode -ne 200) {
      $failed = $true
      $details = $(if ($null -ne $r.StatusCode) { "HTTP {0}" -f $r.StatusCode } else { $r.Error })
      Write-Result "GET /admin/stats (auth)" $false $details
    } else {
      Write-Result "GET /admin/stats (auth)" $true "HTTP 200"
    }
  } catch {
    $failed = $true
    Write-Result "GET /admin/stats (auth)" $false $_.Exception.Message
  }
}

if ($ClientToken) {
  try {
    $headers = @{
      Authorization = "Bearer $ClientToken"
      "Content-Type" = "application/json"
    }
    $payload = @{ url = $ScrapeUrl } | ConvertTo-Json -Compress
    $r = Invoke-HttpJson ("{0}/api/scrape" -f $BaseUrl) "POST" $TimeoutSec $headers $payload "application/json"
    if ($r.StatusCode -ne 200) {
      $failed = $true
      $details = $(if ($null -ne $r.StatusCode) { "HTTP {0}" -f $r.StatusCode } else { $r.Error })
      if ($r.Json -and $r.Json.error -and $r.Json.error.code) {
        $details = "{0}; code={1}" -f $details, $r.Json.error.code
      }
      Write-Result "POST /api/scrape (auth)" $false $details
    } else {
      Write-Result "POST /api/scrape (auth)" $true "HTTP 200"
    }
  } catch {
    $failed = $true
    Write-Result "POST /api/scrape (auth)" $false $_.Exception.Message
  }
}

Write-Host ""
if ($failed) {
  Write-Host "Smoke test failed." -ForegroundColor Red
  exit 1
}

Write-Host "Smoke test passed." -ForegroundColor Green
exit 0

