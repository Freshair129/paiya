param(
    [Parameter(Mandatory = $true)][string]$PythonWindowed,
    [Parameter(Mandatory = $true)][string]$CollectorPython,
    [Parameter(Mandatory = $true)][string]$PaiyaSitePackages,
    [Parameter(Mandatory = $true)][Guid]$PilotRunId,
    [string]$DataRoot = 'F:\paiya-collector-data',
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
foreach ($item in @($PythonWindowed, $CollectorPython)) {
    if (-not (Test-Path -LiteralPath $item -PathType Leaf)) { throw 'Configured Python runtime is unavailable.' }
}
if (-not (Test-Path -LiteralPath $PaiyaSitePackages -PathType Container)) {
    throw 'Configured Paiya dependencies are unavailable.'
}
if (-not $env:PAIYA_DATABASE_URL -or -not $env:PAIYA_COLLECTOR_INBOX -or
    -not $env:PAIYA_FIXTURE_MEDIA_ROOT -or -not $env:PAIYA_PURGE_LOG_PATH) {
    throw 'Local Paiya database and inbox settings are not configured.'
}
$env:PAIYA_DESKTOP_DATA_ROOT = [IO.Path]::GetFullPath($DataRoot)
$env:PAIYA_DESKTOP_PILOT_RUN_ID = $PilotRunId.ToString()
$env:PAIYA_COLLECTOR_PYTHON = [IO.Path]::GetFullPath($CollectorPython)
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = (Join-Path $RepositoryRoot 'collector') + [IO.Path]::PathSeparator +
    (Join-Path $RepositoryRoot 'src') + [IO.Path]::PathSeparator + $PaiyaSitePackages
if ($ValidateOnly) {
    Write-Output 'Desktop environment is configured; no application or browser was started.'
    return
}
Start-Process -FilePath $PythonWindowed -ArgumentList @('-B', '-m', 'paiya_collector.desktop_gui') -WindowStyle Normal
