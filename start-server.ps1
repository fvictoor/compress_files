<#
.SYNOPSIS
    Sobe o servidor do Compressor de Arquivos.

.DESCRIPTION
    Cria o ambiente virtual (.venv) se ele não existir, instala/atualiza as
    dependências quando o requirements.txt mudar e inicia o uvicorn.

.EXAMPLE
    .\start-server.ps1
    .\start-server.ps1 -Port 9000 -MaxUploadMb 8192
    .\start-server.ps1 -BindHost 127.0.0.1 -ForwardedAllowIps "10.0.0.5"
    .\start-server.ps1 -Dev        # recarrega ao salvar arquivos
#>
[CmdletBinding()]
param(
    [string]$BindHost = "0.0.0.0",
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [int]$MaxUploadMb = 4096,
    [int]$MaxFiles = 100,
    [int]$MaxConcurrentJobs = 2,
    [string]$LogDir = "",
    [int]$LogRetentionDays = 90,
    # IPs de proxies reversos confiáveis (para registrar o IP real do usuário no log).
    [string]$ForwardedAllowIps = "127.0.0.1",
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$requirements = Join-Path $root "requirements.txt"
$reqStamp = Join-Path $venv ".requirements.sha256"

function Find-Python {
    foreach ($candidate in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $args_ = @($candidate | Select-Object -Skip 1)
        $version = & $exe @args_ -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -and [version]$version -ge [version]"3.10") {
            return , $candidate
        }
    }
    throw "Python 3.10 ou superior não encontrado. Instale em https://www.python.org/downloads/"
}

# 1. Ambiente virtual
# Se o requirements.txt mudou, recria o .venv do zero: o pip não remove pacotes
# que deixaram de ser necessários (ex.: onnxruntime trazido pelo PyMuPDF 2.0).
$currentHash = (Get-FileHash $requirements -Algorithm SHA256).Hash
$installedHash = if (Test-Path $reqStamp) { (Get-Content $reqStamp -Raw).Trim() } else { "" }
if ((Test-Path $venv) -and $currentHash -ne $installedHash) {
    Write-Host "Dependências mudaram: recriando o ambiente virtual..." -ForegroundColor Cyan
    # Antivírus e indexadores podem segurar arquivos por alguns segundos: tenta de novo.
    $deleted = $false
    for ($attempt = 1; $attempt -le 10 -and -not $deleted; $attempt++) {
        try {
            Remove-Item -Recurse -Force $venv -ErrorAction Stop
            $deleted = $true
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $deleted) {
        $holders = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($venv, [StringComparison]::OrdinalIgnoreCase) }
        $list = ($holders | ForEach-Object { "  PID $($_.ProcessId): $($_.Name)" }) -join "`n"
        if (-not $list) { $list = "  (nenhum processo identificado; tente fechar editores/terminais abertos na pasta)" }
        throw "Não foi possível apagar .venv. Processos usando o ambiente:`n$list`nEncerre-os (ex.: Stop-Process -Id <PID>) e rode o script de novo."
    }
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Criando ambiente virtual em .venv..." -ForegroundColor Cyan
    $py = Find-Python
    $pyArgs = @($py | Select-Object -Skip 1)
    & $py[0] @pyArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o ambiente virtual." }
}

# 2. Dependências (instala quando o ambiente é novo)
if (-not (Test-Path $reqStamp)) {
    Write-Host "Instalando dependências..." -ForegroundColor Cyan
    & $venvPython -m pip install --disable-pip-version-check -q -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependências." }
    Set-Content -Path $reqStamp -Value $currentHash -NoNewline
}

# 3. Configuração da aplicação (lida por app/main.py e app/upload_log.py)
$env:MAX_UPLOAD_MB = $MaxUploadMb
$env:MAX_FILES = $MaxFiles
$env:MAX_CONCURRENT_JOBS = $MaxConcurrentJobs
$env:LOG_RETENTION_DAYS = $LogRetentionDays
if ($LogDir) { $env:LOG_DIR = $LogDir }

# 4. Servidor
# Um único processo: o limite de tarefas simultâneas e a rotação do log são por processo.
$uvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--host", $BindHost,
    "--port", $Port,
    "--proxy-headers",
    "--forwarded-allow-ips", $ForwardedAllowIps
)
if ($Dev) { $uvicornArgs += @("--reload", "--reload-dir", (Join-Path $root "app")) }

$displayHost = if ($BindHost -eq "0.0.0.0") { "localhost" } else { $BindHost }
Write-Host ""
Write-Host "Compressor de Arquivos" -ForegroundColor Green
Write-Host "  Endereço:        http://${displayHost}:$Port"
Write-Host "  Limite upload:   $MaxUploadMb MB / $MaxFiles arquivos"
Write-Host "  Tarefas simult.: $MaxConcurrentJobs"
Write-Host "  Log:             $(if ($LogDir) { $LogDir } else { Join-Path $root 'logs' })\uploads.jsonl"
Write-Host "  Parar:           Ctrl+C"
Write-Host ""

Push-Location $root
try {
    & $venvPython @uvicornArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
