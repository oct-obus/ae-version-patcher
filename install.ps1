# AE Version Patcher - Interactive Installer
# Requires: uv (https://docs.astral.sh/uv/)
#Requires -Version 5.1

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$configFile = Join-Path $scriptDir "ae_patcher_config.txt"
$backupFile = Join-Path $scriptDir "BEE.dll.original"
$patcherScript = Join-Path $scriptDir "ae_version_patcher.py"
$venvDir = Join-Path $scriptDir ".venv"

$defaultAEPath = "C:\Program Files\Adobe\Adobe After Effects 2026\Support Files"

# --- Version data (must match ae_version_patcher.py) ---

$versionEntries = @(
    @{ Version = 13; SaveType = 13; Name = "CC 2014"; Display = "13.x" }
    @{ Version = 14; SaveType = 14; Name = "CC 2017"; Display = "14.x" }
    @{ Version = 15; SaveType = 15; Name = "CC 2018"; Display = "15.x" }
    @{ Version = 16; SaveType = 16; Name = "CC 2019"; Display = "16.x" }
    @{ Version = 17; SaveType = 17; Name = "2020";    Display = "17.x" }
    @{ Version = 18; SaveType = 18; Name = "2021";    Display = "18.x" }
    @{ Version = 22; SaveType = 19; Name = "2022";    Display = "22.x" }
    @{ Version = 23; SaveType = 20; Name = "2023";    Display = "23.x" }
    @{ Version = 24; SaveType = 21; Name = "2024";    Display = "24.x" }
    @{ Version = 25; SaveType = 22; Name = "2025";    Display = "25.x" }
)

# --- Helper functions ---

function Ensure-UvSetup {
    # Check if uv is available
    $uvPath = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvPath) {
        Write-Host ""
        Write-Host "  'uv' is not installed or not in PATH." -ForegroundColor Red
        Write-Host "  Install it from: https://docs.astral.sh/uv/" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Quick install: powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`"" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  Press any key to exit..." -ForegroundColor DarkGray
        [Console]::ReadKey($true) | Out-Null
        exit 1
    }

    # Run uv sync if .venv doesn't exist yet
    if (-not (Test-Path $venvDir)) {
        Write-Host "  First run: installing dependencies via uv sync..." -ForegroundColor Yellow
        Push-Location $scriptDir
        $syncResult = & uv sync 2>&1
        $syncExit = $LASTEXITCODE
        Pop-Location
        if ($syncExit -ne 0) {
            Write-Host "  uv sync failed:" -ForegroundColor Red
            $syncResult | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
            Write-Host ""
            Write-Host "  Press any key to exit..." -ForegroundColor DarkGray
            [Console]::ReadKey($true) | Out-Null
            exit 1
        }
        Write-Host "  Dependencies installed." -ForegroundColor Green
        Write-Host ""
    }
}

function Get-AEPath {
    if (Test-Path $configFile) {
        $saved = Get-Content $configFile -Raw
        $saved = $saved.Trim()
        if ($saved -and (Test-Path $saved)) {
            return $saved
        }
    }
    return $defaultAEPath
}

function Save-AEPath($path) {
    $path | Out-File -FilePath $configFile -Encoding UTF8 -NoNewline
}

function Get-BEEPath {
    $aePath = Get-AEPath
    return Join-Path $aePath "BEE.dll"
}

function Write-Header {
    Clear-Host
    Write-Host ""
    Write-Host "  AE Version Patcher" -ForegroundColor Cyan
    Write-Host "  ==================" -ForegroundColor DarkCyan
    Write-Host ""
    $aePath = Get-AEPath
    $beePath = Get-BEEPath
    if (Test-Path $beePath) {
        Write-Host "  AE Path: $aePath" -ForegroundColor Green
    } else {
        Write-Host "  AE Path: $aePath" -ForegroundColor Red
        Write-Host "  (BEE.dll not found at this path)" -ForegroundColor Red
    }
    if (Test-Path $backupFile) {
        Write-Host "  Backup:  BEE.dll.original exists" -ForegroundColor Green
    } else {
        Write-Host "  Backup:  No backup stored" -ForegroundColor DarkGray
    }
    Write-Host ""
}

function Show-ArrowMenu {
    param(
        [string]$Prompt,
        [string[]]$Items,
        [string[]]$Previews,
        [int]$Default = 0
    )

    $selected = $Default
    $cursorTop = [Console]::CursorTop

    function Render {
        [Console]::SetCursorPosition(0, $cursorTop)
        for ($i = 0; $i -lt $Items.Count; $i++) {
            if ($i -eq $selected) {
                Write-Host "  > $($Items[$i])" -ForegroundColor Cyan -NoNewline
                Write-Host (" " * 40) # clear line remnants
            } else {
                Write-Host "    $($Items[$i])" -NoNewline
                Write-Host (" " * 40)
            }
        }
        Write-Host ""
        if ($Previews -and $Previews[$selected]) {
            # Clear previous preview (up to 5 lines)
            for ($j = 0; $j -lt 5; $j++) {
                Write-Host (" " * 70)
            }
            [Console]::SetCursorPosition(0, $cursorTop + $Items.Count + 1)
            Write-Host "  Save As menu will look like this:" -ForegroundColor DarkGray
            Write-Host ""
            $lines = $Previews[$selected] -split "`n"
            foreach ($line in $lines) {
                Write-Host "    $line" -ForegroundColor Yellow
            }
        }
    }

    Write-Host $Prompt -ForegroundColor White
    Write-Host ""
    Render

    while ($true) {
        $key = [Console]::ReadKey($true)
        switch ($key.Key) {
            "UpArrow"    { if ($selected -gt 0) { $selected-- }; Render }
            "DownArrow"  { if ($selected -lt $Items.Count - 1) { $selected++ }; Render }
            "LeftArrow"  { if ($selected -gt 0) { $selected-- }; Render }
            "RightArrow" { if ($selected -lt $Items.Count - 1) { $selected++ }; Render }
            "Enter"      { return $selected }
            "Escape"     { return -1 }
        }
    }
}

function Request-Elevation {
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Copy-AsAdmin($source, $destination) {
    if (Request-Elevation) {
        Copy-Item -Path $source -Destination $destination -Force
    } else {
        Write-Host ""
        Write-Host "  Administrator privileges required to write to Program Files." -ForegroundColor Yellow
        Write-Host "  Requesting elevation..." -ForegroundColor Yellow
        Write-Host ""

        $copyCmd = "Copy-Item -Path '$source' -Destination '$destination' -Force; Write-Host 'Done.'; Start-Sleep -Seconds 1"
        Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile", "-Command", $copyCmd -Wait
    }
}

# --- Main menu actions ---

function Set-AEInstallPath {
    Write-Header
    $current = Get-AEPath
    Write-Host "  Current path: $current" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Enter new AE Support Files path (or press Enter to keep current):" -ForegroundColor White
    Write-Host ""
    $newPath = Read-Host "  "
    if ($newPath -and $newPath.Trim()) {
        $newPath = $newPath.Trim().Trim('"')
        if (Test-Path (Join-Path $newPath "BEE.dll")) {
            Save-AEPath $newPath
            Write-Host ""
            Write-Host "  Path updated. BEE.dll found." -ForegroundColor Green
        } elseif (Test-Path $newPath) {
            Save-AEPath $newPath
            Write-Host ""
            Write-Host "  Path saved, but BEE.dll was not found there." -ForegroundColor Yellow
            Write-Host "  Make sure the path points to the Support Files folder." -ForegroundColor Yellow
        } else {
            Write-Host ""
            Write-Host "  Path does not exist. Not saved." -ForegroundColor Red
        }
    } else {
        Write-Host ""
        Write-Host "  Path unchanged." -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "  Press any key to continue..." -ForegroundColor DarkGray
    [Console]::ReadKey($true) | Out-Null
}

function Apply-Patch {
    Write-Header

    $beePath = Get-BEEPath
    if (-not (Test-Path $beePath)) {
        Write-Host "  BEE.dll not found at: $beePath" -ForegroundColor Red
        Write-Host "  Use option 1 to set the correct AE installation path." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Press any key to continue..." -ForegroundColor DarkGray
        [Console]::ReadKey($true) | Out-Null
        return
    }

    if (-not (Test-Path $patcherScript)) {
        Write-Host "  ae_version_patcher.py not found in script directory." -ForegroundColor Red
        Write-Host "  Expected at: $patcherScript" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Press any key to continue..." -ForegroundColor DarkGray
        [Console]::ReadKey($true) | Out-Null
        return
    }

    # Build menu items and previews
    $menuItems = @()
    $menuPreviews = @()

    for ($i = 0; $i -lt $versionEntries.Count - 1; $i++) {
        $older = $versionEntries[$i]
        $newer = $versionEntries[$i + 1]
        $menuItems += "AE $($older.Name) ($($older.Display)) / AE $($newer.Name) ($($newer.Display))"
        $menuPreviews += "Save a Copy As $($older.Display)...`nSave a Copy As $($newer.Display)..."
    }

    # Find the index for stock behavior (AE 24/25) to set as default
    $defaultIdx = 0
    for ($i = 0; $i -lt $versionEntries.Count - 1; $i++) {
        if ($versionEntries[$i].Version -eq 24) { $defaultIdx = $i; break }
    }

    $choice = Show-ArrowMenu -Prompt "  Select minimum version (Up/Down to navigate, Enter to select, Esc to cancel):" -Items $menuItems -Previews $menuPreviews -Default $defaultIdx

    if ($choice -eq -1) {
        Write-Host ""
        Write-Host "  Cancelled." -ForegroundColor DarkGray
        Start-Sleep -Seconds 1
        return
    }

    $selectedVersion = $versionEntries[$choice].Version
    $olderEntry = $versionEntries[$choice]
    $newerEntry = $versionEntries[$choice + 1]

    Write-Host ""
    Write-Host ""
    Write-Host "  Applying patch: Save as $($olderEntry.Display) / $($newerEntry.Display)" -ForegroundColor Cyan
    Write-Host ""

    # Create backup if it doesn't exist
    if (-not (Test-Path $backupFile)) {
        Write-Host "  Creating backup: BEE.dll.original" -ForegroundColor Yellow
        Copy-Item -Path $beePath -Destination $backupFile -Force
        Write-Host "  Backup saved." -ForegroundColor Green
    } else {
        Write-Host "  Backup already exists (BEE.dll.original). Skipping backup." -ForegroundColor DarkGray
    }

    # Run the patcher
    $patchedFile = Join-Path $scriptDir "BEE.dll.patched"
    Write-Host "  Running patcher..." -ForegroundColor Yellow

    $patchSource = if (Test-Path $backupFile) { $backupFile } else { $beePath }

    $pythonResult = & uv run --directory $scriptDir python $patcherScript $patchSource $patchedFile --min-version $selectedVersion 2>&1
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        Write-Host ""
        Write-Host "  Patcher failed:" -ForegroundColor Red
        $pythonResult | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        Write-Host ""
        Write-Host "  Press any key to continue..." -ForegroundColor DarkGray
        [Console]::ReadKey($true) | Out-Null
        return
    }

    Write-Host "  Patch created successfully." -ForegroundColor Green
    Write-Host ""

    # Copy patched file to AE directory
    Write-Host "  Copying patched BEE.dll to AE directory..." -ForegroundColor Yellow
    try {
        Copy-AsAdmin $patchedFile $beePath
        Write-Host "  Done! Patched BEE.dll installed." -ForegroundColor Green
    } catch {
        Write-Host "  Failed to copy: $_" -ForegroundColor Red
        Write-Host "  You can manually copy '$patchedFile' to '$beePath'" -ForegroundColor Yellow
    }

    # Clean up temp file
    if (Test-Path $patchedFile) {
        Remove-Item $patchedFile -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "  Launch After Effects and check File > Save As > Save a Copy As" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Press any key to continue..." -ForegroundColor DarkGray
    [Console]::ReadKey($true) | Out-Null
}

function Restore-Original {
    Write-Header

    if (-not (Test-Path $backupFile)) {
        Write-Host "  No backup found (BEE.dll.original)." -ForegroundColor Red
        Write-Host "  Cannot restore without a backup." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  Press any key to continue..." -ForegroundColor DarkGray
        [Console]::ReadKey($true) | Out-Null
        return
    }

    $beePath = Get-BEEPath
    $aePath = Get-AEPath

    Write-Host "  Restoring original BEE.dll..." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Source: $backupFile" -ForegroundColor DarkGray
    Write-Host "  Target: $beePath" -ForegroundColor DarkGray
    Write-Host ""

    try {
        Copy-AsAdmin $backupFile $beePath
        Write-Host "  Original BEE.dll restored successfully." -ForegroundColor Green
    } catch {
        Write-Host "  Failed to restore: $_" -ForegroundColor Red
        Write-Host "  You can manually copy '$backupFile' to '$beePath'" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "  Press any key to continue..." -ForegroundColor DarkGray
    [Console]::ReadKey($true) | Out-Null
}

# --- Main loop ---

Ensure-UvSetup

while ($true) {
    Write-Header

    $mainItems = @(
        "Set AE installation path"
        "Apply 'Save a Copy As' version patch"
        "Restore original BEE.dll"
        "Exit"
    )

    $choice = Show-ArrowMenu -Prompt "  Select an option:" -Items $mainItems

    switch ($choice) {
        0 { Set-AEInstallPath }
        1 { Apply-Patch }
        2 { Restore-Original }
        3 { Clear-Host; exit 0 }
        -1 { Clear-Host; exit 0 }
    }
}
