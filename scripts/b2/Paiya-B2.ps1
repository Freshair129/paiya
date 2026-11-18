<#
.SYNOPSIS
Loads the local B2 environment from this checkout. With -Prune it also runs
both expiry commands, which is the action of the scheduled retention task.

.DESCRIPTION
Maintained source is resolved relative to this file. Local B2 data (inbox,
state, media, purge log and the encrypted database credential) lives under an
external data root: -DataRoot, then PAIYA_B2_DATA_ROOT, then the default.

Dot-source the script to keep the environment in the calling session:

    . .\scripts\b2\Paiya-B2.ps1

-ValidateOnly prints the resolved non-secret settings and reads no credential.
#>
param(
    [switch]$Prune,
    [string]$DataRoot,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'

$b2Repository = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path.TrimEnd('\')
if (-not $DataRoot) { $DataRoot = $env:PAIYA_B2_DATA_ROOT }
if (-not $DataRoot) { $DataRoot = 'F:\paiya-collector-data' }
$b2Data = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
$b2Inside = { param($Path, $Parent) $Path.Equals($Parent, [StringComparison]::OrdinalIgnoreCase) -or
    $Path.StartsWith($Parent + '\', [StringComparison]::OrdinalIgnoreCase) }
if ((& $b2Inside $b2Data $b2Repository) -or (& $b2Inside $b2Repository $b2Data)) {
    throw 'The B2 data root must be outside the repository and must not contain it.'
}
if (-not (Test-Path -LiteralPath $b2Data -PathType Container)) {
    throw 'The B2 data root does not exist.'
}

$b2Python = Join-Path $b2Repository '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $b2Python -PathType Leaf)) {
    throw 'Paiya dependencies are not installed in this checkout. Run "uv sync --locked" at the repository root.'
}

$b2Database = if ($env:PAIYA_B2_DATABASE_NAME) { $env:PAIYA_B2_DATABASE_NAME } else { 'paiya_b2' }
$b2Port = if ($env:PAIYA_B2_DATABASE_PORT) { $env:PAIYA_B2_DATABASE_PORT } else { '55434' }
if ($b2Database -notmatch '^[a-z_][a-z0-9_]*$' -or $b2Port -notmatch '^\d{1,5}$') {
    throw 'PAIYA_B2_DATABASE_NAME or PAIYA_B2_DATABASE_PORT is not valid.'
}

$b2Ops = Join-Path $b2Data 'b2-ops'
$b2State = Join-Path $b2Data 'live-state'
$env:PAIYA_B2_DATA_ROOT = $b2Data
$env:PAIYA_B2_PYTHON = $b2Python
$env:PAIYA_COLLECTOR_INBOX = Join-Path $b2Data 'live-inbox'
$env:PAIYA_FIXTURE_MEDIA_ROOT = Join-Path $b2Data 'b2-media'
$env:PAIYA_PURGE_LOG_PATH = Join-Path $b2Ops 'purge.jsonl'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONNOUSERSITE = '1'

if ($ValidateOnly) {
    [ordered]@{
        repository = $b2Repository
        data_root = $b2Data
        python = $b2Python
        collector_source = Join-Path $b2Repository 'collector'
        paiya_source = Join-Path $b2Repository 'src'
        inbox = $env:PAIYA_COLLECTOR_INBOX
        state = $b2State
        media = $env:PAIYA_FIXTURE_MEDIA_ROOT
        purge_log = $env:PAIYA_PURGE_LOG_PATH
        database = 'postgresql+psycopg://{0}@127.0.0.1:{1}/{0}' -f $b2Database, $b2Port
    } | ConvertTo-Json -Compress
    return
}

# The Collector never receives database credentials: its cleanup runs first,
# before the credential is read into this process.
$b2CollectorCode = 0
if ($Prune) {
    $env:PYTHONPATH = Join-Path $b2Repository 'collector'
    & $b2Python -B -m paiya_collector.cli prune-expired --inbox $env:PAIYA_COLLECTOR_INBOX --state-dir $b2State
    $b2CollectorCode = $LASTEXITCODE
}

$b2CredentialPath = Join-Path $b2Ops 'database-credential.xml'
if (-not (Test-Path -LiteralPath $b2CredentialPath -PathType Leaf)) {
    throw 'The B2 database credential is not available under the data root.'
}
$b2Credential = Import-Clixml -LiteralPath $b2CredentialPath
if ($b2Credential -isnot [pscredential] -or $b2Credential.UserName -ne $b2Database) {
    throw 'The current Windows user must have the B2 database credential.'
}
$b2Password = [Uri]::EscapeDataString($b2Credential.GetNetworkCredential().Password)
$env:PAIYA_DATABASE_URL = 'postgresql+psycopg://{0}:{1}@127.0.0.1:{2}/{0}' -f $b2Database, $b2Password, $b2Port
$env:PYTHONPATH = Join-Path $b2Repository 'src'
Remove-Variable b2Password, b2Credential

if ($Prune) {
    & $b2Python -B -m paiya.cli inbox prune-expired
    $b2PaiyaCode = $LASTEXITCODE
    if ($b2CollectorCode -ne 0 -or $b2PaiyaCode -ne 0) {
        Write-Error "Cleanup reported Collector exit $b2CollectorCode and Paiya exit $b2PaiyaCode."
    }
}
