param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$RunDirectory
)

$ErrorActionPreference = 'Stop'
$CollectorRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PaiyaRoot = (Resolve-Path (Join-Path $CollectorRoot '..')).Path
$RuntimeRoot = Join-Path $CollectorRoot '.uv-python'
$CacheRoot = Join-Path $CollectorRoot '.uv-cache'
$TempRoot = Join-Path $CollectorRoot '.tmp'
$VenvPath = Join-Path $CollectorRoot '.paiya-validator-env'
foreach ($Path in @($RuntimeRoot, $CacheRoot, $TempRoot, $VenvPath)) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}
$env:UV_CACHE_DIR = $CacheRoot
$env:UV_PYTHON_CACHE_DIR = Join-Path $CacheRoot 'python'
$env:UV_PYTHON_INSTALL_DIR = $RuntimeRoot
$env:UV_PYTHON_BIN_DIR = Join-Path $RuntimeRoot 'bin'
$env:UV_PYTHON_INSTALL_BIN = 'false'
$env:UV_PROJECT_ENVIRONMENT = $VenvPath
$env:UV_PYTHON = '3.12'
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:UV_NO_PROGRESS = '1'
$env:UV_OFFLINE = '1'
$env:UV_PYTHON_DOWNLOADS = 'never'

Push-Location $PaiyaRoot
try {
    & uv run --project $PaiyaRoot --frozen python -m paiya.cli inbox validate ([System.IO.Path]::GetFullPath($RunDirectory))
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $ExitCode
