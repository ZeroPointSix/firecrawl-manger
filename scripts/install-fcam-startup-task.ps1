param(
  [Parameter(Mandatory = $false)]
  [string]$TaskName = "FCAM-Server-8000",

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

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$RunScript = Join-Path $RepoRoot "scripts\\run-fcam.ps1"

if (-not (Test-Path -LiteralPath $RunScript)) {
  throw ("run script not found: {0}" -f $RunScript)
}

$args = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", ("`"{0}`"" -f $RunScript),
  "-BindHost", ("`"{0}`"" -f $BindHost),
  "-Port", $Port,
  "-EnvFile", ("`"{0}`"" -f $EnvFile)
)

if ($EnableControlPlane) {
  $args += "-EnableControlPlane"
}
if ($EnableDocs) {
  $args += "-EnableDocs"
}
if ($SkipMigrations) {
  $args += "-SkipMigrations"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($args -join " ") -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host ("Installed scheduled task: {0}" -f $TaskName)
