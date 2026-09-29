<#
.SYNOPSIS
  Removes aw-toolkit autostart and the Claude Code hook.

.DESCRIPTION
  Stops the watchers, deletes their Startup shortcuts and removes the hook entries from
  ~/.claude/settings.json. Your data (watchers\*\data) and ActivityWatch buckets are kept,
  and aw-qt.toml is left as it is.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
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
if ($python -and (Test-Path (Join-Path $HOME '.claude\settings.json'))) {
    & $python (Join-Path $root 'claude-code\hook_setup.py') uninstall
}
Write-Host 'Done. Data folders and ActivityWatch buckets were kept.'
