$ErrorActionPreference = "Stop"

function Invoke-Native {
    param([string[]]$Command)
    $Exe = $Command[0]
    $Args = @($Command | Select-Object -Skip 1)
    & $Exe @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($Command -join ' ')"
    }
}

$App = Join-Path $PSScriptRoot "android_usb_shell_helper"
$Build = Join-Path $App "build"
$Sdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$BuildTools = Join-Path $Sdk "build-tools\36.0.0"
$Platform = Join-Path $Sdk "platforms\android-35\android.jar"
$D8 = Join-Path $BuildTools "d8.bat"

Remove-Item -Recurse -Force $Build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $Build | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Build "classes") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Build "dex") | Out-Null

$Sources = Get-ChildItem -Path (Join-Path $App "src") -Recurse -Filter *.java | Select-Object -ExpandProperty FullName
Invoke-Native (@("javac", "-source", "11", "-target", "11", "-classpath", $Platform, "-d", (Join-Path $Build "classes")) + @($Sources))

$ClassFiles = Get-ChildItem -Path (Join-Path $Build "classes") -Recurse -Filter *.class | Select-Object -ExpandProperty FullName
Invoke-Native (@($D8, "--lib", $Platform, "--output", (Join-Path $Build "dex")) + @($ClassFiles))

Copy-Item (Join-Path $Build "dex\classes.dex") (Join-Path $Build "usb-shell-helper.dex") -Force
Write-Host "Built $Build\usb-shell-helper.dex"
