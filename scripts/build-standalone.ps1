# Genera el ejecutable standalone de Meridia para Windows.
# (Archivo en ASCII puro: PowerShell 5.1 lo lee como ANSI y los caracteres
#  no-ASCII de UTF-8 sin BOM rompen el parseo de cadenas.)
#
# Produce dos artefactos (los usuarios no necesitan Python, Node ni Rust):
#   1. Instalador NSIS:  app\src-tauri\target\release\bundle\nsis\Meridia_*_x64-setup.exe
#   2. ZIP portable:     dist-standalone\Meridia-portable-win64.zip (descomprimir y ejecutar)
#
# Requisitos SOLO en la maquina que compila: Python 3.11+, Node 20+, Rust.
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\build-standalone.ps1
#       [-DiagramsDir <carpeta con .pgdiag para incluir en el ZIP portable>]
#
# El ZIP portable puede llevar una carpeta `diagrams` junto al exe: la app la
# usa como biblioteca inicial (los .pgdiag se resuelven contra la conexion
# actual del usuario, asi que funcionan en otra maquina si su base tiene esas
# tablas). Por defecto se incluyen los de %USERPROFILE%\.pg-diagrammer\diagrams.
param(
    [string]$DiagramsDir = "",
    # Numero de build. Se compone como "<version base>+build.<N>", igual que en
    # CI. Sin valor queda "<version base>+local".
    [string]$BuildNumber = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# --- Version de esta build ----------------------------------------------------
$baseVersion = (Get-Content "$root\app\src-tauri\tauri.conf.json" -Raw |
    ConvertFrom-Json).version
if ($BuildNumber) {
    $appVersion = "$baseVersion+build.$BuildNumber"
} else {
    $appVersion = "$baseVersion+local"
}
# La lee el frontend por import.meta.env para mostrarla en la pantalla inicial.
$env:VITE_APP_VERSION = $appVersion
Write-Host ""
Write-Host "Version de esta build: $appVersion" -ForegroundColor Cyan

# Procesos de builds/pruebas anteriores bloquean los exe (el onefile de
# PyInstaller lanza un proceso hijo con el mismo nombre): se cierran primero.
Get-Process -Name "Meridia", "pg-diagrammer", "pg-diagrammer-sidecar" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

# --- 1. Sidecar Python -> ejecutable unico (PyInstaller) ---------------------
Write-Host ""
Write-Host "[1/3] Empaquetando sidecar Python con PyInstaller..." -ForegroundColor Cyan
Set-Location "$root\sidecar"

# Ruta absoluta: mas abajo se usa desde la raiz del repo, no solo desde sidecar.
$venvPy = "$root\sidecar\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    python -m venv .venv
}
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -e "."
& $venvPy -m pip install --quiet pyinstaller

# --collect-all cubre los imports dinamicos: uvicorn (loops/protocols),
# keyring (backends por entry-points), psycopg_binary (libpq) y pytds.
& $venvPy -m PyInstaller `
    --onefile `
    --name pg-diagrammer-sidecar `
    --collect-all uvicorn `
    --collect-all keyring `
    --collect-all psycopg `
    --collect-all psycopg_binary `
    --collect-all pytds `
    --noconfirm --clean `
    scripts\pyinstaller_entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fallo" }

# Prueba de humo: el exe arranca y responde el handshake JSON en stdout.
$env:PG_DIAGRAMMER_TOKEN = "build-smoke"
$proc = Start-Process -FilePath "dist\pg-diagrammer-sidecar.exe" -PassThru `
    -RedirectStandardOutput "dist\smoke-out.txt" -NoNewWindow
Start-Sleep -Seconds 6
# Matar por nombre ademas del PID: el bootloader onefile crea un hijo
# con el mismo nombre que de otro modo queda vivo y bloquea el exe.
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Get-Process -Name "pg-diagrammer-sidecar" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$smoke = Get-Content "dist\smoke-out.txt" -Raw -ErrorAction SilentlyContinue
if ($smoke -notmatch '"port"') { throw "El sidecar empaquetado no respondio el handshake: $smoke" }
Write-Host "Sidecar OK: $($smoke.Trim())"

# --- 2. Colocarlo donde Tauri lo espera (externalBin con target triple) ------
Write-Host ""
Write-Host "[2/3] Preparando binario para Tauri..." -ForegroundColor Cyan
$triple = (rustc -vV | Select-String "host: (.+)").Matches[0].Groups[1].Value
$binDir = "$root\app\src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
Copy-Item "dist\pg-diagrammer-sidecar.exe" "$binDir\pg-diagrammer-sidecar-$triple.exe" -Force

# --- 3. Build de la app Tauri (instalador + exe) -----------------------------
Write-Host ""
Write-Host "[3/3] Compilando la app Tauri (release)..." -ForegroundColor Cyan
Set-Location "$root\app"
if (-not (Test-Path "node_modules")) { npm install }
npm run tauri build -- --config src-tauri/tauri.standalone.json
if ($LASTEXITCODE -ne 0) { throw "tauri build fallo" }

# --- ZIP portable ------------------------------------------------------------
$out = "$root\dist-standalone"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$release = "$root\app\src-tauri\target\release"
$portable = "$out\Meridia-portable-win64"
if (Test-Path $portable) { Remove-Item $portable -Recurse -Force }
New-Item -ItemType Directory -Path $portable | Out-Null
Copy-Item "$release\pg-diagrammer.exe" "$portable\Meridia.exe"
Copy-Item "$release\pg-diagrammer-sidecar.exe" "$portable\pg-diagrammer-sidecar.exe"

# Documentacion de la build: se genera despues de compilar para poder listar
# los archivos reales, y viaja dentro del ZIP portable.
Set-Location $root
& $venvPy "scripts\release_notes.py" --version $appVersion
if ($LASTEXITCODE -ne 0) { throw "No se pudo generar NOVEDADES.md" }
Copy-Item "$out\NOVEDADES.md" "$portable\NOVEDADES.md" -Force

# Diagramas incluidos en el portable (biblioteca inicial).
$diagSrc = $DiagramsDir
if (-not $diagSrc) {
    $userDiagrams = "$env:USERPROFILE\.pg-diagrammer\diagrams"
    if (Test-Path $userDiagrams) { $diagSrc = $userDiagrams }
}
if ($diagSrc -and (Test-Path $diagSrc)) {
    $pgdiags = Get-ChildItem "$diagSrc\*.pgdiag" -ErrorAction SilentlyContinue
    if ($pgdiags.Count -gt 0) {
        New-Item -ItemType Directory -Force -Path "$portable\diagrams" | Out-Null
        Copy-Item "$diagSrc\*.pgdiag" "$portable\diagrams\" -Force
        Write-Host "  Diagramas incluidos: $($pgdiags.Count) (desde $diagSrc)"
    }
}

Compress-Archive -Path "$portable\*" -DestinationPath "$out\Meridia-portable-win64.zip" -Force

Write-Host ""
Write-Host "Listo: $appVersion" -ForegroundColor Green
Get-ChildItem "$release\bundle\nsis\*-setup.exe" -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host "  Instalador: $($_.FullName)" }
Write-Host "  Portable:   $out\Meridia-portable-win64.zip"
Write-Host "  Novedades:  $out\NOVEDADES.md"
Write-Host ""
Write-Host "Nota: la version portable requiere WebView2 (preinstalado en Windows 10/11;"
Write-Host "el instalador NSIS lo descarga solo si falta)."
