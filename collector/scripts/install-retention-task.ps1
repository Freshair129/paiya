param(
    [string]$Inbox = 'F:\paiya-collector-data\live-inbox',
    [string]$StateDir = 'F:\paiya-collector-data\live-state',
    [string]$TaskName = 'PaiyaCollectorRetention-owner-group-01'
)

$ErrorActionPreference = 'Stop'
$CollectorRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RepositoryRoot = (Resolve-Path (Join-Path $CollectorRoot '..')).Path

function Get-NormalizedPath([string]$Path) {
    $FullPath = [System.IO.Path]::GetFullPath($Path)
    $Root = [System.IO.Path]::GetPathRoot($FullPath)
    if ($FullPath.Equals($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $Root
    }
    return $FullPath.TrimEnd('\')
}

$InboxPath = Get-NormalizedPath $Inbox
$StatePath = Get-NormalizedPath $StateDir
$ProjectPath = Get-NormalizedPath $RepositoryRoot
$ProjectPrefix = $ProjectPath + '\'

function Assert-NoReparseComponents([string]$Path, [string]$Label) {
    $Root = [System.IO.Path]::GetPathRoot($Path)
    $Current = $Root
    $Remainder = $Path.Substring($Root.Length)
    foreach ($Component in $Remainder.Split([char[]]'\', [System.StringSplitOptions]::RemoveEmptyEntries)) {
        $Current = Join-Path $Current $Component
        if (Test-Path -LiteralPath $Current) {
            $Attributes = (Get-Item -LiteralPath $Current -Force).Attributes
            if (($Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label must not traverse a symlink or junction."
            }
        }
    }
}

foreach ($Path in @($InboxPath, $StatePath)) {
    if ($Path.Equals([System.IO.Path]::GetPathRoot($Path), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Inbox and state directories cannot be a volume root.'
    }
    if ($Path.Equals($ProjectPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $Path.StartsWith($ProjectPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        $ProjectPath.StartsWith($Path.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Inbox and state directories must remain separate from and outside the paiya repository.'
    }
}
Assert-NoReparseComponents -Path $InboxPath -Label 'Inbox path'
Assert-NoReparseComponents -Path $StatePath -Label 'State path'
if ($InboxPath.Equals($StatePath, [System.StringComparison]::OrdinalIgnoreCase) -or
    $InboxPath.StartsWith($StatePath + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
    $StatePath.StartsWith($InboxPath + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Inbox and state directories must be separate and must not contain each other.'
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw "Task '$TaskName' already exists. Inspect or remove it manually before choosing another name."
}

$RunnerPath = Join-Path $CollectorRoot 'scripts\run-collector.ps1'
$ArgumentLine = '-NoProfile -File "{0}" prune-expired --inbox "{1}" --state-dir "{2}"' -f `
    $RunnerPath, $InboxPath, $StatePath
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $ArgumentLine
$DailyAt = Get-Date -Hour 3 -Minute 0 -Second 0
$Triggers = @(
    (New-ScheduledTaskTrigger -Daily -At $DailyAt),
    (New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name))
)
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$Principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Principal $Principal `
    -Description 'Deletes expired Paiya Collector bundles and derived state only; never launches a browser or contacts a source.' | Out-Null

Write-Output "Installed cleanup-only task '$TaskName' for the current Windows user."
Write-Output 'It runs at 3:00 AM and at logon, using the Collector retention command only.'
