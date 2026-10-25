<#
Stage source into a dedicated Windows VM build directory and prepare its Python
environment. This does not build or replace an application package. Run as the
interactive Windows user when Python was installed from the Microsoft Store.

Example from macOS (Parallels shared Home enabled):
  prlctl exec 'Windows 11' --current-user powershell -NoProfile -ExecutionPolicy Bypass -File \\Mac\Home\Developer\MIT_Rkt_Team\RT_Telemetry_UI\scripts\build_windows_vm.ps1 -SourceRoot \\Mac\Home\Developer\MIT_Rkt_Team\RT_Telemetry_UI -InstallDependencies
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [string]$StageRoot = 'C:\RocketGNCBuild\station-network',
    [string]$BootstrapPython = 'python',
    [string]$WindowsVendorRoot = '',
    [switch]$InstallDependencies,
    [switch]$RunNetworkTests
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if (-not (Test-Path (Join-Path $SourceRoot 'pyproject.toml'))) {
    throw 'SourceRoot must point to the telemetry repository.'
}
$stageMarker = Join-Path $StageRoot '.rocket-gnc-vm-stage'
if ((Test-Path $StageRoot) -and -not (Test-Path $stageMarker)) {
    throw 'Refusing to modify an existing unmarked directory. Choose a new StageRoot.'
}
New-Item -ItemType Directory -Force $StageRoot | Out-Null
Set-Content -Path $stageMarker -Value 'Dedicated telemetry source/build staging directory.' -Encoding ASCII

function Copy-SourceTree([string]$Source, [string]$Destination) {
    & robocopy $Source $Destination /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__ .pytest_cache .ruff_cache /XF '*.pyc' '.DS_Store'
    if ($LASTEXITCODE -ge 8) { throw "Could not stage $Source (robocopy $LASTEXITCODE)." }
}

foreach ($directory in @('src', 'resources', 'scripts', 'tests', 'docs', 'packaging', 'references', 'simulation_bridge')) {
    Copy-SourceTree (Join-Path $SourceRoot $directory) (Join-Path $StageRoot $directory)
}
foreach ($filename in @('pyproject.toml', 'requirements-build.txt', 'uv.lock', 'README.md', 'Makefile', 'Makefile.windows', 'Makefile.macos')) {
    Copy-Item -Force (Join-Path $SourceRoot $filename) (Join-Path $StageRoot $filename)
}

$vendor = Join-Path $StageRoot 'vendor'
New-Item -ItemType Directory -Force $vendor | Out-Null
# JARs are platform-independent. Never copy the Mac Java or SRT runtime here.
foreach ($filename in @('openrocket.jar', 'rocket-bridge.jar', 'engine.json', 'source-build.json')) {
    $source = Join-Path (Join-Path $SourceRoot 'vendor') $filename
    if (Test-Path $source) { Copy-Item -Force $source (Join-Path $vendor $filename) }
}
$mediaSource = Join-Path (Join-Path $SourceRoot 'vendor') 'network-media'
if (Test-Path $mediaSource) {
    $mediaTarget = Join-Path $vendor 'network-media'
    New-Item -ItemType Directory -Force $mediaTarget | Out-Null
    Get-ChildItem $mediaSource -File | Where-Object { $_.Extension -eq '.dll' -or $_.Name -like '*LICENSE*' } | ForEach-Object {
        Copy-Item -Force $_.FullName (Join-Path $mediaTarget $_.Name)
    }
}
if ($WindowsVendorRoot) {
    $manifest = Join-Path $WindowsVendorRoot 'java-runtime.json'
    $java = Get-Content -Raw $manifest | ConvertFrom-Json
    if ($java.os -ne 'windows' -or $java.arch -ne 'x64') {
        throw 'WindowsVendorRoot must contain a Windows x64 Java runtime.'
    }
    Copy-SourceTree (Join-Path $WindowsVendorRoot 'java') (Join-Path $vendor 'java')
    Copy-Item -Force $manifest (Join-Path $vendor 'java-runtime.json')
}

$python = Join-Path $StageRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    & $BootstrapPython -m venv (Join-Path $StageRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated build environment.' }
}
& $python -c "import sys,sysconfig; assert (3,12) <= sys.version_info[:2] < (3,15); assert sysconfig.get_platform() == 'win-amd64', 'Windows requires x64 Python'; print(sys.executable)"
if ($LASTEXITCODE -ne 0) { throw 'Build environment must use x64 Python 3.12-3.14.' }
Push-Location $StageRoot
try {
    if ($InstallDependencies) {
        & $python -m pip install -r requirements-build.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        & $python -m pip install --no-deps -e .
        if ($LASTEXITCODE -ne 0) { throw 'Project installation failed.' }
    }
    if ($RunNetworkTests) {
        $env:QT_QPA_PLATFORM = 'offscreen'
        & $python -m pytest -q tests/test_network_observation.py tests/test_network_display.py tests/test_network_runtime.py tests/test_network_configuration.py tests/test_network_configuration_ui.py
        if ($LASTEXITCODE -ne 0) { throw 'Windows network checks failed.' }
    }
} finally {
    Pop-Location
}
Write-Output "Windows source ready: $StageRoot"
Write-Output "Python: $python"
