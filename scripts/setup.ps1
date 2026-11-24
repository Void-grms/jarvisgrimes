# Instalación de Jarvis local (Windows 10/11). Se ejecuta desde instalar.bat.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # la barra de progreso vuelve lentisimas las descargas en PowerShell 5
$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz
$Py = Join-Path $Raiz ".venv\Scripts\python.exe"

function Paso($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }

# 1. Python 3.12 (las librerías de voz tienen versiones probadas para 3.12)
Paso "Python 3.12"
$tienePy = $false
try { & py -3.12 -c "print(1)" *> $null; $tienePy = ($LASTEXITCODE -eq 0) } catch {}
if (-not $tienePy) {
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    & py -3.12 -c "print(1)" *> $null
    if ($LASTEXITCODE -ne 0) { throw "Python se instaló, pero hay que reabrir la terminal. Cierra esta ventana y vuelve a ejecutar instalar.bat" }
}

# 2. Ollama (motor del modelo local)
Paso "Ollama"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    winget install -e --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
    $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
}
try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 2 | Out-Null }
catch { Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep 4 }

# 3. Entorno virtual + librerías
Paso "Librerías de Python (tarda unos minutos la primera vez)"
if (-not (Test-Path $Py)) { & py -3.12 -m venv .venv }
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Falló pip install" }

# 4. Configuración
if (-not (Test-Path config.toml)) { Copy-Item config.ejemplo.toml config.toml }
$Modelo = & $Py -c "from asistente.config import cargar; print(cargar()['llm']['modelo'])"
$Whisper = & $Py -c "from asistente.config import cargar; print(cargar()['stt']['modelo'])"

# 5. Modelo de lenguaje
Paso "Descargando modelo $Modelo (~1-2 GB)"
ollama pull $Modelo

# 6. Voz en español (Piper)
Paso "Voz en español"
New-Item -ItemType Directory -Force voces | Out-Null
$base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/ald/medium"
foreach ($f in "es_MX-ald-medium.onnx", "es_MX-ald-medium.onnx.json") {
    $dest = "voces\$f"
    $min = if ($f -like "*.json") { 1000 } else { 50000000 }   # detecta descargas incompletas
    if (-not (Test-Path $dest) -or (Get-Item $dest).Length -lt $min) {
        curl.exe -L --fail --retry 3 -o $dest "$base/$f"
        if ($LASTEXITCODE -ne 0) { throw "No pude descargar $f" }
    }
}

# 7. Modelo de reconocimiento de voz (se descarga una vez)
Paso "Reconocimiento de voz (Whisper $Whisper)"
& $Py -c "from faster_whisper import WhisperModel; WhisperModel('$Whisper', device='cpu', compute_type='int8')"

# 8. Arranque automático al iniciar sesión, desbloquear y volver de suspensión
Paso "Arranque automático"
& (Join-Path $PSScriptRoot "instalar_inicio.ps1")

Write-Host "`nListo. Siguientes pasos:" -ForegroundColor Green
Write-Host "  1) conectar-clementina.bat   (pegar el enlace del calendario)"
Write-Host "  2) conectar-gmail.bat        (necesita credentials.json, ver README)"
Write-Host "  3) probar-latencia.bat       (mide si responde en 2-3 s)"
Write-Host "  4) iniciar.bat               (o reinicia sesión y te saludará solo)"
