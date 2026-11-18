<#
.SYNOPSIS
Starts the loopback-only B2 dashboard (API and built web app) from this checkout.
#>
param(
    [ValidateRange(1, 65535)][int]$Port = 8766,
    [string]$DataRoot,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$webDist = Join-Path $repository 'web\dist'
if (-not (Test-Path -LiteralPath (Join-Path $webDist 'index.html') -PathType Leaf)) {
    throw 'The web app is not built in this checkout. Run "pnpm --dir web install --frozen-lockfile" and "pnpm --dir web build".'
}
if ($ValidateOnly) {
    . (Join-Path $PSScriptRoot 'Paiya-B2.ps1') -DataRoot $DataRoot -ValidateOnly
    Write-Output 'Dashboard environment is configured; no server was started.'
    return
}

. (Join-Path $PSScriptRoot 'Paiya-B2.ps1') -DataRoot $DataRoot
$env:PAIYA_PORT = [string]$Port
$env:PAIYA_DEV_FIXTURE_MODE = '0'
$env:PAIYA_WEB_DIST = $webDist
Remove-Item Env:\PAIYA_READ_CONTEXT_GROUP_KEY -ErrorAction SilentlyContinue
& $env:PAIYA_B2_PYTHON -B -m paiya.serve
exit $LASTEXITCODE
