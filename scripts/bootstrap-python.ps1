param(
  [Parameter(Mandatory = $false)]
  [string]$PythonVersion = "3.11.8"
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $RepoRoot

$ToolsDir = Join-Path $RepoRoot ".tools"
$PythonRoot = Join-Path $ToolsDir "python"
$PythonHome = Join-Path $PythonRoot $PythonVersion
$PythonExe = Join-Path $PythonHome "tools/python.exe"

New-Item -ItemType Directory -Force -Path $PythonHome | Out-Null

if (-not (Test-Path -LiteralPath $PythonExe)) {
  $Pkg = Join-Path $PythonRoot ("python.{0}.nupkg" -f $PythonVersion)
  New-Item -ItemType Directory -Force -Path $PythonRoot | Out-Null

  if (-not (Test-Path -LiteralPath $Pkg)) {
    $Url = "https://www.nuget.org/api/v2/package/python/$PythonVersion"
    Write-Host ("Downloading portable python: {0}" -f $Url)
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Pkg
  }

  Write-Host ("Extracting: {0}" -f $Pkg)
  $Zip = Join-Path $PythonRoot ("python.{0}.zip" -f $PythonVersion)
  Copy-Item -Force -LiteralPath $Pkg -Destination $Zip
  Expand-Archive -Force -LiteralPath $Zip -DestinationPath $PythonHome
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
  throw ("Python bootstrap failed, not found: {0}" -f $PythonExe)
}

$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts/python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
  & $PythonExe -m venv $VenvDir
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r "requirements.txt" -r "requirements-dev.txt"

Write-Host "Running tests with coverage gate..."
& $VenvPython -m pytest --cov=app --cov-fail-under=80
