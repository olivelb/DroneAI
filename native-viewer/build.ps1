param(
  [string]$BuildDirectory = "$env:USERPROFILE\Documents\DroneAI\native-viewer-build",
  [string]$OutputDirectory = "$env:USERPROFILE\Documents\DroneAI\GSTileViewer",
  [string]$PortableSdk = ""
)
$ErrorActionPreference = "Stop"
$configure = @("-S", $PSScriptRoot, "-B", $BuildDirectory)
if ($PortableSdk) {
  $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
  $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
  if (!$vs) { throw "Visual Studio C++ toolchain not found" }
  $vc = Get-ChildItem -LiteralPath "$vs\VC\Tools\MSVC" -Directory | Sort-Object Name -Descending | Select-Object -First 1
  $sdk = Join-Path $PortableSdk "microsoft.windows.sdk.cpp\c"
  $libs = Join-Path $PortableSdk "microsoft.windows.sdk.cpp.x64\c"
  $version = (Get-ChildItem -LiteralPath "$sdk\Include" -Directory | Sort-Object Name -Descending | Select-Object -First 1).Name
  $env:PATH = "$($vc.FullName)\bin\Hostx64\x64;$sdk\bin\$version\x64;$env:PATH"
  $env:INCLUDE = "$($vc.FullName)\include;$sdk\Include\$version\ucrt;$sdk\Include\$version\shared;$sdk\Include\$version\um;$sdk\Include\$version\winrt"
  $env:LIB = "$($vc.FullName)\lib\x64;$libs\ucrt\x64;$libs\um\x64"
  $configure += @("-G", "NMake Makefiles", "-DCMAKE_BUILD_TYPE=Release")
} else {
  $fxc = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\fxc.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
  if ($fxc) { $env:PATH = "$($fxc.DirectoryName);$env:PATH" }
  $configure += @("-G", "Visual Studio 17 2022", "-A", "x64")
}
cmake @configure
if ($LASTEXITCODE) { throw "CMake configuration failed" }
cmake --build $BuildDirectory --config Release --parallel
if ($LASTEXITCODE) { throw "Build failed" }
ctest --test-dir $BuildDirectory -C Release --output-on-failure
if ($LASTEXITCODE) { throw "Contract tests failed" }
cmake --install $BuildDirectory --config Release --prefix $OutputDirectory
if ($LASTEXITCODE) { throw "Packaging failed" }
Write-Host "Viewer: $OutputDirectory\GSTileViewer.exe"
