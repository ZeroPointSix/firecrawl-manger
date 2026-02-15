param(
  [Parameter(Mandatory = $false)]
  [string]$BindHost = "0.0.0.0",

  [Parameter(Mandatory = $false)]
  [int]$Port = 8000,

  [Parameter(Mandatory = $false)]
  [string]$EnvFile = ".env",

  [Parameter(Mandatory = $false)]
  [switch]$EnableControlPlane,

  [Parameter(Mandatory = $false)]
  [switch]$EnableDocs,

  [Parameter(Mandatory = $false)]
  [switch]$SkipMigrations
)

$ErrorActionPreference = "Stop"

function Import-DotEnvIfPresent([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  $lines = Get-Content -LiteralPath $Path -ErrorAction Stop
  foreach ($line in $lines) {
    $trimmed = ([string]$line).Trim()
    if (-not $trimmed) {
      continue
    }
    if ($trimmed.StartsWith("#")) {
      continue
    }

    $normalized = $trimmed
    if ($normalized.StartsWith("export ")) {
      $normalized = $normalized.Substring(7).Trim()
    }

    $idx = $normalized.IndexOf("=")
    if ($idx -lt 1) {
      continue
    }

    $key = $normalized.Substring(0, $idx).Trim()
    $value = $normalized.Substring($idx + 1).Trim()

    if (-not $key) {
      continue
    }

    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
      $value = $value.Substring(1, $value.Length - 2)
    }

    if (Test-Path -LiteralPath ("env:{0}" -f $key)) {
      continue
    }

    Set-Item -LiteralPath ("env:{0}" -f $key) -Value $value
  }
}

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $RepoRoot

Import-DotEnvIfPresent (Join-Path $RepoRoot $EnvFile)

$env:FCAM_SERVER__ENABLE_DATA_PLANE = "true"
$env:FCAM_SERVER__ENABLE_CONTROL_PLANE = $(if ($EnableControlPlane) { "true" } else { "false" })
$env:FCAM_SERVER__ENABLE_DOCS = $(if ($EnableDocs) { "true" } else { "false" })

if (-not (Test-Path -LiteralPath "env:FCAM_MASTER_KEY")) {
  Write-Warning "FCAM_MASTER_KEY 未配置：/readyz 与数据面鉴权将返回 NOT_READY（建议在 .env 或环境变量中设置）"
}

if ($EnableControlPlane -and -not (Test-Path -LiteralPath "env:FCAM_ADMIN_TOKEN")) {
  Write-Warning "FCAM_ADMIN_TOKEN 未配置：启用控制面时 /admin/* 将返回 NOT_READY（建议在 .env 或环境变量中设置）"
}

$PythonExe = Join-Path $RepoRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
  throw ("Python venv not found: {0}. Please run scripts/bootstrap-python.ps1" -f $PythonExe)
}

if (-not $SkipMigrations) {
  & $PythonExe -m alembic upgrade head
}

& $PythonExe -m uvicorn "app.main:app" --host $BindHost --port $Port
