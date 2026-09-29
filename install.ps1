<#
.SYNOPSIS
  Installs (or updates) aw-toolkit for the current Windows user.

.DESCRIPTION
  Copies the watchers and the Claude Code hook from this repository to an install folder
  (default: ~\.aw-toolkit) and runs them from there, so the repository can be moved, edited
  or deleted without breaking anything. Then:
  - adds aw-watcher-away and aw-watcher-output to the Startup folder (pythonw, no console) and starts them
  - registers the Claude Code hook in ~/.claude/settings.json (if ~/.claude exists)
  - makes aw-qt autostart aw-watcher-input, unless aw-qt.toml already sets autostart_modules

  Re-run after `git pull` or local changes: code is replaced, data\ and config.toml are kept.
  Data from older layouts (watchers running from the repo, hook state in ~/.claude/hooks/aw_state)
  is moved into the install folder once.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoClaudeHook
#>
param(
    [string]$InstallDir = (Join-Path $HOME '.aw-toolkit'),
    [switch]$NoAway,
    [switch]$NoOutput,
    [switch]$NoClaudeHook,
    [switch]$NoAwQt
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
$pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $python -or -not $pythonw) { throw 'python / pythonw not found in PATH. Install Python 3.11 or newer.' }
& $python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required (tomllib).' }

$startup = [Environment]::GetFolderPath('Startup')
$watchers = @(
    @{ Name = 'aw-watcher-away';   Script = 'aw_watcher_away.py';   Skip = $NoAway;
       Description = 'Asks what you were doing after you were away' },
    @{ Name = 'aw-watcher-output'; Script = 'aw_watcher_output.py'; Skip = $NoOutput;
       Description = 'Records saved files and commits of active projects' }
)

function Stop-Watcher([string]$scriptName) {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like "*$scriptName*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Move-Once([string]$from, [string]$to) {
    # migrate legacy data without ever overwriting installed data: a missing target is moved whole,
    # into an existing folder only the files it doesn't have yet; conflicts stay at the old place
    if (-not (Test-Path $from)) { return }
    if (-not (Test-Path $to)) {
        New-Item -ItemType Directory -Force (Split-Path $to) | Out-Null
        Move-Item $from $to
        Write-Host "  > moved $from -> $to"
        return
    }
    if (-not (Test-Path $from -PathType Container)) { return }
    foreach ($f in Get-ChildItem $from -File) {
        if (-not (Test-Path (Join-Path $to $f.Name))) { Move-Item $f.FullName $to }
    }
    if (Get-ChildItem $from) {
        Write-Host "  ! $from not fully migrated (same files exist in $to) - check and delete it by hand"
    } else {
        Remove-Item $from
        Write-Host "  > merged $from -> $to"
    }
}

Write-Host "aw-toolkit install -> $InstallDir"
New-Item -ItemType Directory -Force $InstallDir | Out-Null

foreach ($w in $watchers) {
    if ($w.Skip) { continue }
    $src = Join-Path $repo "watchers\$($w.Name)"
    $dst = Join-Path $InstallDir $w.Name
    Stop-Watcher $w.Script
    New-Item -ItemType Directory -Force $dst | Out-Null
    Copy-Item (Join-Path $src $w.Script), (Join-Path $src 'config.example.toml') $dst -Force
    Move-Once (Join-Path $src 'data') (Join-Path $dst 'data')              # repo-run layout (v0.1)
    Move-Once (Join-Path $src 'config.toml') (Join-Path $dst 'config.toml')

    $lnk = Join-Path $startup "$($w.Name).lnk"
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $s.TargetPath = $pythonw
    $s.Arguments = "`"$(Join-Path $dst $w.Script)`""
    $s.WorkingDirectory = $dst
    $s.Description = $w.Description
    $s.Save()
    Start-Process -FilePath $lnk
    Write-Host "  + $($w.Name)  (Startup shortcut, running)"
}

# the hook, its installer and the uninstaller live in the install folder too,
# so everything keeps working - and can be removed - after the repository is gone
$cc = Join-Path $InstallDir 'claude-code'
New-Item -ItemType Directory -Force $cc | Out-Null
Copy-Item (Join-Path $repo 'claude-code\aw_claude_hook.py'), (Join-Path $repo 'claude-code\hook_setup.py') $cc -Force
Copy-Item (Join-Path $repo 'uninstall.ps1') $InstallDir -Force
Move-Once (Join-Path $HOME '.claude\hooks\aw_state') (Join-Path $cc 'state')  # hook state before v0.2

if (-not $NoClaudeHook) {
    if (Test-Path (Join-Path $HOME '.claude')) {
        & $python (Join-Path $cc 'hook_setup.py') install (Join-Path $cc 'aw_claude_hook.py')
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

$commit = ''
if (Get-Command git -ErrorAction SilentlyContinue) {
    $commit = (& git -C $repo rev-parse --short HEAD 2>$null)
    if (& git -C $repo status --porcelain 2>$null) { $commit += ' (with uncommitted changes)' }
}
@"
This folder is an INSTALLED COPY of aw-toolkit - do not edit the code here.
Edit the repository and run its install.ps1 again; data\ and config.toml here are kept.

Installed from: $repo
Commit:         $commit
Installed at:   $(Get-Date -Format 'yyyy-MM-dd HH:mm')

Uninstall: powershell -ExecutionPolicy Bypass -File "$InstallDir\uninstall.ps1" [-Purge]
"@ | Set-Content -Path (Join-Path $InstallDir 'INSTALL-INFO.txt') -Encoding ASCII

Write-Host 'Done.'
