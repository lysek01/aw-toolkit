<#
.SYNOPSIS
  Removes aw-toolkit autostart and the Claude Code hook.

.DESCRIPTION
  Stops the watchers, deletes their Startup shortcuts and removes the hook entries from
  ~/.claude/settings.json. The install folder (~\.aw-toolkit) with your data and config is kept
  unless you pass -Purge. ActivityWatch buckets and aw-qt.toml are never touched.

  A copy of this script is placed in the install folder, so it works without the repository.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File "$HOME\.aw-toolkit\uninstall.ps1"
  powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 -Purge
#>
param(
    [string]$InstallDir = (Join-Path $HOME '.aw-toolkit'),
    [switch]$Purge
)
$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')

Write-Host 'aw-toolkit uninstall'
foreach ($w in @(@('aw-watcher-away', 'aw_watcher_away.py'), @('aw-watcher-output', 'aw_watcher_output.py'))) {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like "*$($w[1])*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $lnk = Join-Path $startup "$($w[0]).lnk"
    if (Test-Path $lnk) { Remove-Item $lnk }
    Write-Host "  - $($w[0]) stopped, Startup shortcut removed"
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
$setup = @((Join-Path $InstallDir 'claude-code\hook_setup.py'), (Join-Path $PSScriptRoot 'claude-code\hook_setup.py')) |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($python -and $setup -and (Test-Path (Join-Path $HOME '.claude\settings.json'))) {
    & $python $setup uninstall
}

if ($Purge -and (Test-Path $InstallDir)) {
    Remove-Item $InstallDir -Recurse -Force
    Write-Host "  - removed $InstallDir (code, data, config)"
} else {
    Write-Host "Done. $InstallDir (data, config) was kept - use -Purge to delete it."
}
