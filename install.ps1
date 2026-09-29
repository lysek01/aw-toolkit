<#
.SYNOPSIS
  Installs aw-toolkit for the current Windows user.

.DESCRIPTION
  - adds aw-watcher-away and aw-watcher-output to the Startup folder (run via pythonw, no console)
  - registers the Claude Code hook in ~/.claude/settings.json (if Claude Code is used)
  - makes aw-qt autostart aw-watcher-input, unless aw-qt.toml already sets autostart_modules
  - starts the watchers right away (stopping older instances first)

  Safe to run again after pulling updates. Nothing is written outside your user profile.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoClaudeHook
#>
param(
    [switch]$NoAway,
    [switch]$NoOutput,
    [switch]$NoClaudeHook,
    [switch]$NoAwQt
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
$pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $python -or -not $pythonw) { throw 'python / pythonw not found in PATH. Install Python 3.11 or newer.' }
& $python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required (tomllib).' }

$startup = [Environment]::GetFolderPath('Startup')

function Stop-Watcher([string]$scriptName) {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like "*$scriptName*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Install-Watcher([string]$name, [string]$script, [string]$description) {
    $path = Join-Path $root "watchers\$name\$script"
    Stop-Watcher $script
    $lnk = Join-Path $startup "$name.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $pythonw
    $s.Arguments = "`"$path`""
    $s.WorkingDirectory = Split-Path $path
    $s.Description = $description
    $s.Save()
    Start-Process -FilePath $lnk
    Write-Host "  + $name  (Startup shortcut, running)"
}

Write-Host 'aw-toolkit install'
if (-not $NoAway) {
    Install-Watcher 'aw-watcher-away' 'aw_watcher_away.py' 'Asks what you were doing after you were away'
}
if (-not $NoOutput) {
    Install-Watcher 'aw-watcher-output' 'aw_watcher_output.py' 'Records saved files and commits of active projects'
}

if (-not $NoClaudeHook) {
    if (Test-Path (Join-Path $HOME '.claude')) {
        & $python (Join-Path $root 'claude-code\hook_setup.py') install
    } else {
        Write-Host '  - Claude Code not found (~/.claude), hook skipped'
    }
}

if (-not $NoAwQt) {
    $toml = Join-Path $env:LOCALAPPDATA 'activitywatch\activitywatch\aw-qt\aw-qt.toml'
    if (Test-Path $toml) {
        $text = [IO.File]::ReadAllText($toml)
        $section = [regex]::Match($text, '(?s)\[aw-qt\](.*?)(?=\r?\n\[|$)').Groups[1].Value
        if ($section -match '(?m)^\s*autostart_modules') {
            Write-Host '  - aw-qt.toml already sets autostart_modules, left unchanged'
        } else {
            $line = 'autostart_modules = ["aw-server", "aw-watcher-afk", "aw-watcher-window", "aw-watcher-input"]'
            $text = $text -replace '\[aw-qt\]', "[aw-qt]`r`n$line"
            [IO.File]::WriteAllText($toml, $text)  # UTF-8 without BOM
            Write-Host '  + aw-qt.toml: aw-watcher-input added to autostart (restart ActivityWatch)'
        }
    } else {
        Write-Host '  - aw-qt.toml not found, is ActivityWatch installed and started once?'
    }
}
Write-Host 'Done.'
