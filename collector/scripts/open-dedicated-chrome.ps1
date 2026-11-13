param(
    [string]$ProfileDir = 'F:\paiya-collector-data\browser-profiles\owner-group-01'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$GroupKey = 'owner-group-01'
$ProfileDir = [System.IO.Path]::GetFullPath($ProfileDir)
$DataRoot = 'F:\paiya-collector-data'
$TempRoot = Join-Path $DataRoot 'browser-temp'

if ([System.IO.Path]::GetPathRoot($ProfileDir) -ne [System.IO.Path]::GetPathRoot($ProjectRoot)) {
    throw 'The dedicated Chrome profile must be on the Collector volume.'
}
$RepoPrefix = $ProjectRoot.TrimEnd('\') + '\'
if ($ProfileDir.StartsWith($RepoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The dedicated Chrome profile must be outside the repository.'
}

$DefaultProfiles = @(
    (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Google\Chrome\User Data'),
    (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Microsoft\Edge\User Data')
)
foreach ($DefaultProfile in $DefaultProfiles) {
    $DefaultPath = [System.IO.Path]::GetFullPath($DefaultProfile).TrimEnd('\')
    if ($ProfileDir.Equals($DefaultPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $ProfileDir.StartsWith($DefaultPath + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Everyday Chrome/Edge profiles are not allowed.'
    }
}

if (Test-Path -LiteralPath $ProfileDir) {
    $MarkerPath = Join-Path $ProfileDir 'PAIYA_COLLECTOR_PROFILE'
    if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) {
        $ExistingItems = @(Get-ChildItem -LiteralPath $ProfileDir -Force)
        if ($ExistingItems.Count -gt 0) {
            throw 'Refusing to reuse a non-empty unmarked browser profile.'
        }
        Set-Content -LiteralPath $MarkerPath -Value $GroupKey -Encoding Ascii -NoNewline
    }
    elseif ((Get-Content -LiteralPath $MarkerPath -Raw).Trim() -ne $GroupKey) {
        throw 'The profile marker belongs to another group.'
    }
}
else {
    New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
    Set-Content -LiteralPath (Join-Path $ProfileDir 'PAIYA_COLLECTOR_PROFILE') -Value $GroupKey -Encoding Ascii -NoNewline
}

$ActivePort = Join-Path $ProfileDir 'DevToolsActivePort'
if (Test-Path -LiteralPath $ActivePort) {
    throw 'This profile already has a DevToolsActivePort file. Close or inspect its existing Chrome process before starting another.'
}

$ProgramFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
$ChromeCandidates = @(
    (Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $ProgramFilesX86 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Google\Chrome\Application\chrome.exe')
)
$ChromePath = $ChromeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $ChromePath) {
    throw 'Google Chrome was not found in the standard Windows installation locations.'
}

New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
$Arguments = @(
    "--user-data-dir=`"$ProfileDir`"",
    '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port=0',
    '--no-first-run',
    '--no-default-browser-check',
    'about:blank'
)
$PreviousTemp = $env:TEMP
$PreviousTmp = $env:TMP
try {
    $env:TEMP = $TempRoot
    $env:TMP = $TempRoot
    $ChromeProcess = Start-Process -FilePath $ChromePath -ArgumentList $Arguments -PassThru -WindowStyle Normal
}
finally {
    $env:TEMP = $PreviousTemp
    $env:TMP = $PreviousTmp
}

$Deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $Deadline) {
    if (Test-Path -LiteralPath $ActivePort -PathType Leaf) {
        $PortLines = Get-Content -LiteralPath $ActivePort
        if ($PortLines.Count -ge 2 -and $PortLines[0] -match '^\d+$' -and $PortLines[1] -match '^/devtools/browser/') {
            Write-Output "Visible dedicated Chrome is open for $GroupKey. Sign in manually in this window; the Collector will navigate only after a confirmed manual run."
            Write-Output "Profile: $ProfileDir"
            Write-Output "Loopback DevTools port: $($PortLines[0])"
            exit 0
        }
        throw 'Chrome created a malformed DevToolsActivePort file.'
    }
    if ($ChromeProcess.HasExited) {
        throw 'Chrome exited before its dedicated DevTools endpoint became available.'
    }
    Start-Sleep -Milliseconds 250
}
throw 'Chrome did not expose its local DevTools endpoint within 15 seconds.'
