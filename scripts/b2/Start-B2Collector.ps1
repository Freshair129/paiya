<#
.SYNOPSIS
Opens the local Collector control window for one owner-approved run ID, using
only source and installed environments from this checkout.

.DESCRIPTION
The window does not open a browser or contact a source by itself. The run ID
comes from the owner's written approval; it is never stored in the repository.
#>
param(
    [Parameter(Mandatory = $true)][Guid]$PilotRunId,
    [string]$DataRoot,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$collectorScripts = Join-Path $repository 'collector\.venv\Scripts'
if (-not (Test-Path -LiteralPath (Join-Path $collectorScripts 'pythonw.exe') -PathType Leaf)) {
    throw 'The Collector environment is not installed in this checkout. Run ".\collector\scripts\setup-browser.ps1".'
}

# Dot-sourcing shares this scope, so keep the caller's switch under another name.
$validateDesktopOnly = [bool]$ValidateOnly
. (Join-Path $PSScriptRoot 'Paiya-B2.ps1') -DataRoot $DataRoot
& (Join-Path $repository 'collector\scripts\start-desktop.ps1') `
    -PythonWindowed (Join-Path $collectorScripts 'pythonw.exe') `
    -CollectorPython (Join-Path $collectorScripts 'python.exe') `
    -PaiyaSitePackages (Join-Path $repository '.venv\Lib\site-packages') `
    -PilotRunId $PilotRunId `
    -DataRoot $env:PAIYA_B2_DATA_ROOT `
    -ValidateOnly:$validateDesktopOnly
