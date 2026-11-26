param(
    [string]$TargetDir = (Join-Path $env:USERPROFILE ".gemini\config\plugins\context-anchor"),
    [switch]$Uninstall,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
    if (Test-Path -LiteralPath $TargetDir) {
        Remove-Item -LiteralPath $TargetDir -Recurse -Force
        Write-Output "Removed plugin from: $TargetDir"
    } else {
        Write-Output "Plugin directory not found: $TargetDir"
    }
    Write-Output "Note: Persistent hook state (if any) in `$env:USERPROFILE\.gemini\antigravity\plugin_data\context-anchor (and legacy context-anchor-state) was preserved."
    exit 0
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "plugin.json"))) {
    throw "Cannot locate plugin.json under $RepoRoot"
}

$ParentDir = Split-Path -Parent $TargetDir
if (-not (Test-Path -LiteralPath $ParentDir)) {
    New-Item -ItemType Directory -Path $ParentDir -Force | Out-Null
}

if (Test-Path -LiteralPath $TargetDir) {
    if ($Force) {
        Remove-Item -LiteralPath $TargetDir -Recurse -Force
    } else {
        # Refresh managed plugin files cleanly without touching external state directories
        Get-ChildItem -LiteralPath $TargetDir -Force | ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
        }
    }
} else {
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
}

$RootFiles = @("plugin.json", "hooks.json", "config.default.json", "LICENSE", "README.md")
foreach ($file in $RootFiles) {
    $src = Join-Path $RepoRoot $file
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $TargetDir $file) -Force
    }
}

$Dirs = @("assets", "rules", "skills")
foreach ($dir in $Dirs) {
    $srcDir = Join-Path $RepoRoot $dir
    if (Test-Path -LiteralPath $srcDir) {
        Copy-Item -LiteralPath $srcDir -Destination (Join-Path $TargetDir $dir) -Recurse -Force
    }
}

$ScriptsTarget = Join-Path $TargetDir "scripts"
New-Item -ItemType Directory -Path $ScriptsTarget -Force | Out-Null
$ScriptFiles = @("common.py", "pre_invocation.py", "ledger_tool.py", "install.ps1", "install.sh")
foreach ($sf in $ScriptFiles) {
    $src = Join-Path $PSScriptRoot $sf
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $ScriptsTarget $sf) -Force
    }
}

Write-Output "Installed Context Anchor plugin to: $TargetDir"
Write-Output "Open Antigravity Desktop App -> Customizations -> Installed -> Context Anchor to verify."
