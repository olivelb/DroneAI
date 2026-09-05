param(
  [Parameter(Mandatory=$true)][string]$HarnessDirectory,
  [Parameter(Mandatory=$true)][string]$BundleDirectory,
  [Parameter(Mandatory=$true)][string]$NativeReport,
  [Parameter(Mandatory=$true)][string]$OutputReport
)
$ErrorActionPreference = "Stop"
$arguments = @(
  (Join-Path $PSScriptRoot "serve-webgpu.mjs"),
  $HarnessDirectory, $BundleDirectory, $NativeReport, $OutputReport
) | ForEach-Object { '"' + $_ + '"' }
$p = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput "$OutputReport.server.log" -RedirectStandardError "$OutputReport.server-error.log"
Write-Output "Local benchmark server PID: $($p.Id)"
Write-Output "http://127.0.0.1:8768/?dev=1"
