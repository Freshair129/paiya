param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]]$CollectorArgs
)

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
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:UV_NO_PROGRESS = '1'
$env:UV_OFFLINE = '1'
$env:UV_PYTHON_DOWNLOADS = 'never'

Push-Location $ProjectRoot
try {
    $UvRunArgs = @('--project', $ProjectRoot, '--python', '3.12', '--managed-python', '--locked', '--no-dev')
    if ($CollectorArgs.Count -gt 0 -and $CollectorArgs[0] -eq 'discover-browser') {
        $UvRunArgs += @('--extra', 'browser')
    }
    & uv run @UvRunArgs python -m paiya_collector.cli @CollectorArgs
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $ExitCode
