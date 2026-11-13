$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RuntimeRoot = Join-Path $ProjectRoot '.uv-python'
$CacheRoot = Join-Path $ProjectRoot '.uv-cache'
$TempRoot = Join-Path $ProjectRoot '.tmp'
$VenvPath = Join-Path $ProjectRoot '.venv'

foreach ($Path in @($RuntimeRoot, $CacheRoot, $TempRoot, $VenvPath)) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

$env:UV_CACHE_DIR = $CacheRoot
$env:UV_PYTHON_CACHE_DIR = Join-Path $CacheRoot 'python'
$env:UV_PYTHON_INSTALL_DIR = $RuntimeRoot
$env:UV_PYTHON_BIN_DIR = Join-Path $RuntimeRoot 'bin'
$env:UV_PYTHON_INSTALL_BIN = 'false'
$env:UV_PROJECT_ENVIRONMENT = $VenvPath
$env:UV_OFFLINE = '0'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:UV_NO_PROGRESS = '1'

Push-Location $ProjectRoot
try {
    & uv sync --project $ProjectRoot --python 3.12 --managed-python --locked --no-dev --extra browser
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($ExitCode -ne 0) {
    exit $ExitCode
}
Write-Output 'Playwright Python client is installed in the F: project environment. No browser binary was downloaded.'
