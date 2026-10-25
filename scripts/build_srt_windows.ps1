param([string]$Python = "", [string]$Architecture = "x64")
# Native developer prerequisite: Visual Studio 2022 C++ tools and its CMake.
# The produced application includes this runtime and needs no separate install.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $Root '.venv\Scripts\python.exe' }
$VsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$Vs = & $VsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $Vs) { throw 'Install Visual Studio C++ build tools before preparing the native developer runtime.' }
$CMake = Join-Path $Vs 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
$Work = Join-Path $Root 'build\network-native'
$Destination = Join-Path $Root 'vendor\network-media'
New-Item -ItemType Directory -Force $Work, $Destination | Out-Null
$Archive = Join-Path $Work 'srt-1.5.4.tar.gz'
$Expected = 'd0a8b600fe1b4eaaf6277530e3cfc8f15b8ce4035f16af4a5eb5d4b123640cdd'
if (-not (Test-Path $Archive)) {
    Invoke-WebRequest 'https://github.com/Haivision/srt/archive/refs/tags/v1.5.4.tar.gz' -OutFile $Archive
}
if ((Get-FileHash $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Expected) { throw 'SRT source digest mismatch' }
& tar -xzf $Archive -C $Work
if ($LASTEXITCODE) { throw 'SRT source extraction failed' }
$Source = Join-Path $Work 'srt-1.5.4'
$Build = Join-Path $Work 'srt-build'
& $CMake -S $Source -B $Build -G 'Visual Studio 17 2022' -A $Architecture -DENABLE_ENCRYPTION=OFF -DENABLE_APPS=OFF -DENABLE_SHARED=ON -DENABLE_STATIC=OFF
if ($LASTEXITCODE) { throw 'SRT configuration failed' }
& $CMake --build $Build --config Release --parallel 4
if ($LASTEXITCODE) { throw 'SRT compilation failed' }
Copy-Item (Join-Path $Build 'Release\srt.dll') $Destination -Force
Copy-Item (Join-Path $Source 'LICENSE') (Join-Path $Destination 'SRT-LICENSE') -Force
$Crt = Get-ChildItem (Join-Path $Vs 'VC\Redist\MSVC') -Recurse -Directory -Filter Microsoft.VC143.CRT |
    Where-Object { $_.FullName -match ('\\' + $Architecture + '\\') -and $_.FullName -notmatch '\\onecore\\' } |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $Crt) { throw 'Visual C++ redistributable DLL directory not found' }
foreach ($Name in 'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll') {
    Copy-Item (Join-Path $Crt.FullName $Name) $Destination -Force
}
& $Python (Join-Path $PSScriptRoot 'prepare_network_media.py') --srt-library $Destination
if ($LASTEXITCODE) { throw 'Native runtime verification failed' }
