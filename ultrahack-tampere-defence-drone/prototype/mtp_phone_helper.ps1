param(
    [ValidateSet('List', 'CopyApk', 'PullLogs', 'PullCapture', 'All')]
    [string] $Action = 'List',
    [string] $PhoneNamePattern = 'Armor 28 Ultra',
    [string] $PackageName = 'com.yegmina.thermallivedebug',
    [string] $ApkPath = 'prototype\android_thermal_live_debug\build\thermal-live-debug.apk',
    [string] $OutDir = 'prototype\mtp_pulled_logs'
)

$ErrorActionPreference = 'Stop'

function New-ShellApp {
    New-Object -ComObject Shell.Application
}

function Get-PhoneStorage {
    param([string] $Pattern)

    $shell = New-ShellApp
    $computer = $shell.Namespace(17)
    if ($null -eq $computer) {
        throw 'Could not open Windows shell namespace for This PC.'
    }

    $phone = $computer.Items() |
        Where-Object { $_.Name -match $Pattern } |
        Select-Object -First 1
    if ($null -eq $phone) {
        throw "No MTP phone matched pattern '$Pattern'."
    }

    $storage = $phone.GetFolder.Items() | Select-Object -First 1
    if ($null -eq $storage) {
        throw "Phone '$($phone.Name)' has no visible shared storage."
    }

    [pscustomobject]@{
        Shell = $shell
        Phone = $phone
        StorageItem = $storage
        Storage = $storage.GetFolder
    }
}

function Find-MtpChild {
    param(
        [Parameter(Mandatory = $true)] $Folder,
        [Parameter(Mandatory = $true)] [string] $Name,
        [switch] $Regex
    )

    if ($Regex) {
        return $Folder.Items() |
            Where-Object { $_.Name -match $Name } |
            Select-Object -First 1
    }

    $Folder.Items() |
        Where-Object { $_.Name -eq $Name } |
        Select-Object -First 1
}

function Get-PackageFilesFolder {
    param($Storage, [string] $Package)

    $androidItem = Find-MtpChild $Storage 'Android'
    if ($null -eq $androidItem) { throw 'Android folder not visible over MTP.' }
    $android = $androidItem.GetFolder

    $dataItem = Find-MtpChild $android 'data'
    if ($null -eq $dataItem) { throw 'Android\data folder not visible over MTP.' }
    $data = $dataItem.GetFolder

    $packageItem = Find-MtpChild $data $Package
    if ($null -eq $packageItem) { throw "Package folder '$Package' not visible over MTP." }
    $packageFolder = $packageItem.GetFolder

    $filesItem = Find-MtpChild $packageFolder 'files'
    if ($null -eq $filesItem) { throw "Package '$Package' has no visible files folder." }

    $filesItem.GetFolder
}

function Wait-ForLocalFiles {
    param(
        [string] $Path,
        [int] $ExpectedAtLeast,
        [int] $TimeoutSeconds = 45,
        [string] $Filter = '*'
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 500
        $items = @(Get-ChildItem -Path $Path -Recurse -Filter $Filter -ErrorAction SilentlyContinue)
    } while ($items.Count -lt $ExpectedAtLeast -and (Get-Date) -lt $deadline)

    $items
}

function Copy-ApkToPhone {
    param($Storage, [string] $Apk)

    $resolved = Resolve-Path -LiteralPath $Apk
    $downloadItem = Find-MtpChild $Storage '^(Download|Downloads)$' -Regex
    if ($null -eq $downloadItem) { throw 'Could not find phone Download folder over MTP.' }

    $download = $downloadItem.GetFolder
    Write-Output "Copying APK to phone Download: $($resolved.Path)"
    $download.CopyHere($resolved.Path, 16)

    Start-Sleep -Seconds 3
    $copied = Find-MtpChild $download (Split-Path -Leaf $resolved.Path)
    if ($null -eq $copied) {
        throw 'Copy command returned, but APK is not visible in Download yet.'
    }
    Write-Output "APK visible on phone: $($downloadItem.Name)\$($copied.Name)"
}

function Pull-ThermalLogs {
    param($Storage, [string] $Package, [string] $OutputRoot)

    $files = Get-PackageFilesFolder $Storage $Package
    $sessions = @($files.Items() |
        Where-Object { $_.Name -like 'thermal_live_debug_*' } |
        Sort-Object Name)
    if ($sessions.Count -eq 0) {
        Write-Output "No thermal_live_debug_* folders visible for $Package."
        return
    }

    $destRoot = Join-Path (Resolve-Path .).Path (
        Join-Path $OutputRoot ("thermallivedebug_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    )
    New-Item -ItemType Directory -Force -Path $destRoot | Out-Null
    $destShell = (New-ShellApp).Namespace($destRoot)

    foreach ($session in $sessions) {
        Write-Output "Pulling log session: $($session.Name)"
        $destShell.CopyHere($session, 16)
    }

    $logs = Wait-ForLocalFiles $destRoot $sessions.Count 45 'thermal_live_debug.log'
    Write-Output "Pulled $($logs.Count)/$($sessions.Count) logs to $destRoot"
    $logs | Sort-Object FullName | ForEach-Object { Write-Output $_.FullName }
}

function Pull-ScreenCapture {
    param($Storage, [string] $Package, [string] $OutputRoot)

    $files = Get-PackageFilesFolder $Storage $Package
    $captureItem = Find-MtpChild $files 'screen_capture'
    if ($null -eq $captureItem) {
        Write-Output "No screen_capture folder visible for $Package."
        return
    }

    $capture = $captureItem.GetFolder
    $items = @($capture.Items())
    if ($items.Count -eq 0) {
        Write-Output 'screen_capture folder is empty.'
        return
    }

    $destRoot = Join-Path (Resolve-Path .).Path (
        Join-Path $OutputRoot ("screen_capture_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    )
    New-Item -ItemType Directory -Force -Path $destRoot | Out-Null
    $destShell = (New-ShellApp).Namespace($destRoot)

    foreach ($item in $items) {
        Write-Output "Pulling capture file: $($item.Name)"
        $destShell.CopyHere($item, 16)
    }

    $local = Wait-ForLocalFiles $destRoot $items.Count 45
    Write-Output "Pulled $($local.Count)/$($items.Count) capture files to $destRoot"
    $local | Sort-Object FullName | ForEach-Object { Write-Output $_.FullName }
}

function Show-PhoneSummary {
    param($Storage, [string] $Package)

    Write-Output 'Phone storage root:'
    $Storage.Items() | ForEach-Object { Write-Output "  $($_.Name)" }

    try {
        $files = Get-PackageFilesFolder $Storage $Package
        Write-Output "Visible files for ${Package}:"
        $files.Items() | Sort-Object Name | ForEach-Object {
            Write-Output ("  {0} folder={1}" -f $_.Name, $_.IsFolder)
        }
    } catch {
        Write-Output $_.Exception.Message
    }
}

$ctx = Get-PhoneStorage $PhoneNamePattern
Write-Output "MTP phone: $($ctx.Phone.Name)"
Write-Output "MTP storage: $($ctx.StorageItem.Name)"

switch ($Action) {
    'List' { Show-PhoneSummary $ctx.Storage $PackageName }
    'CopyApk' { Copy-ApkToPhone $ctx.Storage $ApkPath }
    'PullLogs' { Pull-ThermalLogs $ctx.Storage $PackageName $OutDir }
    'PullCapture' { Pull-ScreenCapture $ctx.Storage $PackageName $OutDir }
    'All' {
        Copy-ApkToPhone $ctx.Storage $ApkPath
        Pull-ThermalLogs $ctx.Storage $PackageName $OutDir
        Pull-ScreenCapture $ctx.Storage $PackageName $OutDir
    }
}
