<#
Item Code Studio - local Windows installer (Agent H, agents/AGENT_H_DEPLOY.md task 6).

Copies the app to a per-user folder (no admin rights needed), writes a
config.json with the ledger pointed at a server ("client" mode) and NO
secret of any kind, creates a Start Menu + Desktop shortcut, and installs
the app's Python dependencies from requirements.txt (pymupdf, paddleocr,
etc.) - degrading gracefully with a clear warning, never failing the whole
install, if that step can't complete.

    powershell -ExecutionPolicy Bypass -File install\install.ps1 `
        -ServerUrl "https://items.example.com"

Run it from inside a checked-out copy of the repo (it copies ITS OWN parent
folder as the source). Target machines are now assumed to have internet and
admin rights, a deliberate change (7 August 2026) from this project's
original "no pip install" constraint - see agents/CONTRACTS.md house rule 1
for the full reasoning. If that assumption is wrong for a given machine,
this step warns and continues; the app still runs, just without OCR/PDF
extraction until requirements.txt is installed by hand.

WHAT "client" MODE MEANS TODAY, HONESTLY: core/tier.py's three-tier
resolver is fully built and unit-tested (see HANDOVER.md), but the small
startup patch that wires it into server.py has not landed yet (that file is
Agent 0's exclusively - see core/tier.py's module docstring for the exact
3-line patch needed). Until it does, an installed copy started with
`run.bat` still opens its OWN local database directly rather than truly
talking to -ServerUrl. To make that failure mode obviously safe rather than
silently wrong, this installer deliberately does NOT copy data\itemcode.db
- a freshly installed copy starts with an EMPTY database (seed.py never
having been run against it), which is unmistakably not the real ledger,
rather than a full duplicate 889-group copy that could be mistaken for one.
#>

[CmdletBinding()]
param(
    [string]$ServerUrl = "",
    [string]$InstallDir = "$env:LOCALAPPDATA\ItemCodeStudio",
    [switch]$NoShortcuts
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Warn2($msg) { Write-Host "    WARNING: $msg" -ForegroundColor Yellow }
function Write-Ok($msg)   { Write-Host "    ok: $msg" -ForegroundColor Green }

Write-Host ""
Write-Host "Item Code Studio - installer" -ForegroundColor White
Write-Host "============================"
Write-Host ""

# ---------------------------------------------------------------- 1. Python
Write-Step "Checking for Python"
$python = $null
foreach ($cmd in @("python", "py")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source; break }
}
if (-not $python) {
    Write-Host ""
    Write-Host "    Python was not found on this machine." -ForegroundColor Red
    Write-Host "    Item Code Studio needs Python 3.10 or later to run." -ForegroundColor Red
    Write-Host "    Install it from https://python.org/downloads (tick 'Add to PATH')" -ForegroundColor Red
    Write-Host "    then run this installer again. Nothing has been changed." -ForegroundColor Red
    Write-Host ""
    exit 1
}
$verOut = & $python --version 2>&1
Write-Ok "$verOut ($python)"

# --------------------------------------------------------- 2. dependencies
# pymupdf (PDF text/tables) + paddleocr/paddlepaddle (OCR) replace the old
# optional external Tesseract-OCR binary. Same "degrade, don't fail" policy
# as before, just via pip instead of a system installer: a failure here is
# a warning, not an install-stopping error - the app still runs and typed
# text / text-layer PDFs / decoding / search all still work without it.
Write-Step "Installing Python dependencies (pymupdf, paddleocr - internet required)"
try {
    $reqFile = Join-Path $RepoRoot "requirements.txt"
    & $python -m pip install -q -r $reqFile 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "requirements.txt installed"
    } else {
        throw "pip exited with code $LASTEXITCODE"
    }
} catch {
    Write-Warn2 "could not install requirements.txt ($_)."
    Write-Warn2 "Scanned/photographed invoices and PDF extraction will not work;"
    Write-Warn2 "everything else (typed text, decoding, search) still works."
    Write-Warn2 "Retry later with: pip install -r requirements.txt"
}

# ------------------------------------------------------------------ 3. copy
Write-Step "Copying application to $InstallDir"
if (Test-Path $InstallDir) {
    Write-Warn2 "install folder already exists - files will be updated in place, config.json left untouched if present"
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Everything needed to run the app; nothing that's only useful to whoever
# is building it (planning docs, source workbooks, the seeded dev database,
# test suite, the installer's own source).
$include = @("server.py", "manage.py", "run.bat", "seed.py", "README.md",
             "requirements.txt", "core", "routes", "web")
foreach ($item in $include) {
    $src = Join-Path $RepoRoot $item
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $InstallDir $item
    if (Test-Path $src -PathType Container) {
        robocopy $src $dst /MIR /XD "__pycache__" /NFL /NDL /NJH /NJS /NC /NS | Out-Null
    } else {
        Copy-Item $src $dst -Force
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "exports") | Out-Null
Write-Ok "copied core, routes, web, server.py, manage.py, run.bat, seed.py"

# --------------------------------------------------------- 4. config.json
# No secret, ever - just an address. The LLM key and every ERPNext
# credential live only in the VPS's `settings` table (agents/CONTRACTS.md
# §2 and §5), never here, never in this installer, never in git.
Write-Step "Writing config.json (client mode, no secrets)"
$cfgPath = Join-Path $InstallDir "config.json"
$config = [ordered]@{
    app_name    = "Item Code Studio"
    host        = "127.0.0.1"
    port        = 8756
    match_threshold = 60
    llm         = [ordered]@{ provider = "none"; model = ""; api_key = ""; base_url = "" }
    erpnext     = [ordered]@{ enabled = $false; dry_run = $true; base_url = ""; username = ""; password = "" }
    ledger      = [ordered]@{
        mode        = "client"
        server_url  = $ServerUrl
        local_url   = ""
        lease_size  = 10
    }
    backup      = [ordered]@{ drive_folder = ""; daily_keep = 14; weekly_keep = 8 }
}
$config | ConvertTo-Json -Depth 6 | Set-Content -Path $cfgPath -Encoding utf8
Write-Ok "wrote $cfgPath  (ledger.server_url = '$ServerUrl')"
if (-not $ServerUrl) {
    Write-Warn2 "no -ServerUrl was given - edit config.json's ledger.server_url before relying on this install"
}

# ------------------------------------------------------------- 5. shortcuts
if (-not $NoShortcuts) {
    Write-Step "Creating shortcuts"
    $wsh = New-Object -ComObject WScript.Shell
    $target = Join-Path $InstallDir "run.bat"

    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnk1 = $wsh.CreateShortcut((Join-Path $desktop "Item Code Studio.lnk"))
    $lnk1.TargetPath = $target
    $lnk1.WorkingDirectory = $InstallDir
    $lnk1.Description = "Item Code Studio"
    $lnk1.Save()
    Write-Ok "Desktop shortcut created"

    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    $lnk2 = $wsh.CreateShortcut((Join-Path $startMenu "Item Code Studio.lnk"))
    $lnk2.TargetPath = $target
    $lnk2.WorkingDirectory = $InstallDir
    $lnk2.Description = "Item Code Studio"
    $lnk2.Save()
    Write-Ok "Start Menu shortcut created (current user, no admin rights used)"
}

Write-Host ""
Write-Host "Done. Installed to: $InstallDir" -ForegroundColor Green
Write-Host "Launch it from the Desktop or Start Menu shortcut, or run.bat directly." -ForegroundColor Green
Write-Host ""
