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

$App = Join-Path $PSScriptRoot "android_thermal_live_debug"
$Build = Join-Path $App "build"
$Sdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$BuildTools = Join-Path $Sdk "build-tools\36.0.0"
$Platform = Join-Path $Sdk "platforms\android-35\android.jar"

$Aapt2 = Join-Path $BuildTools "aapt2.exe"
$D8 = Join-Path $BuildTools "d8.bat"
$Zipalign = Join-Path $BuildTools "zipalign.exe"
$Apksigner = Join-Path $BuildTools "apksigner.bat"
$Keytool = "C:\Program Files\Java\jdk-23\bin\keytool.exe"
if (!(Test-Path $Keytool)) {
    $Keytool = "C:\Program Files\Android\Android Studio\jbr\bin\keytool.exe"
}

Remove-Item -Recurse -Force $Build -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $Build | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Build "classes") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Build "dex") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Build "compiled") | Out-Null

Invoke-Native @($Aapt2, "compile", "--dir", (Join-Path $App "res"), "-o", (Join-Path $Build "compiled"))
$FlatFiles = Get-ChildItem -Path (Join-Path $Build "compiled") -Filter *.flat | Select-Object -ExpandProperty FullName
Invoke-Native (@($Aapt2, "link",
    "-o", (Join-Path $Build "base-unsigned.apk"),
    "-I", $Platform,
    "--manifest", (Join-Path $App "AndroidManifest.xml")) +
    @($FlatFiles) +
    @("--java", (Join-Path $Build "gen")))

$Sources = Get-ChildItem -Path (Join-Path $App "src") -Recurse -Filter *.java | Select-Object -ExpandProperty FullName
$Generated = Get-ChildItem -Path (Join-Path $Build "gen") -Recurse -Filter *.java | Select-Object -ExpandProperty FullName
$JavaSources = @($Sources) + @($Generated)
Invoke-Native (@("javac", "-source", "11", "-target", "11", "-classpath", $Platform, "-d", (Join-Path $Build "classes")) + @($JavaSources))

$ClassFiles = Get-ChildItem -Path (Join-Path $Build "classes") -Recurse -Filter *.class | Select-Object -ExpandProperty FullName
Invoke-Native (@($D8, "--lib", $Platform, "--output", (Join-Path $Build "dex")) + @($ClassFiles))
Compress-Archive -Path (Join-Path $Build "dex\classes.dex") -DestinationPath (Join-Path $Build "classes.zip") -Force
Copy-Item (Join-Path $Build "base-unsigned.apk") (Join-Path $Build "with-dex.apk") -Force
& tar -xf (Join-Path $Build "classes.zip") -C $Build
& powershell -NoProfile -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; `$zip=[IO.Compression.ZipFile]::Open('$Build\with-dex.apk','Update'); `$entry=[IO.Compression.ZipFileExtensions]::CreateEntryFromFile(`$zip,'$Build\classes.dex','classes.dex'); `$zip.Dispose()"

$Keystore = Join-Path $App "debug.keystore"
if (!(Test-Path $Keystore)) {
    Invoke-Native @($Keytool, "-genkeypair", "-v", "-keystore", $Keystore, "-storepass", "android", "-keypass", "android", "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-dname", "CN=Android Debug,O=Yegmina,C=FI")
}
Invoke-Native @($Zipalign, "-f", "-p", "4", (Join-Path $Build "with-dex.apk"), (Join-Path $Build "aligned.apk"))
Invoke-Native @($Apksigner, "sign", "--ks", $Keystore, "--ks-pass", "pass:android", "--key-pass", "pass:android", "--out", (Join-Path $Build "thermal-live-debug.apk"), (Join-Path $Build "aligned.apk"))
Invoke-Native @($Apksigner, "verify", (Join-Path $Build "thermal-live-debug.apk"))

Write-Host "Built $Build\thermal-live-debug.apk"
