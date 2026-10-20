<#
.SYNOPSIS
    Gera o instalador do Compressor de Arquivos (dist\CompressorArquivos-Setup-<versão>.exe).

.DESCRIPTION
    1. Usa um ambiente virtual próprio (.venv-build) só com as dependências do app
       e o PyInstaller, para não empacotar pacotes desnecessários.
    2. Gera o app no modo pasta (dist\CompressorArquivos): nada é extraído para o
       %TEMP% ao abrir, então não sobra lixo mesmo se o processo for morto.
    3. Empacota a pasta em um único instalador com o Inno Setup.

.EXAMPLE
    .\build-exe.ps1
    .\build-exe.ps1 -Version 1.1.0
#>
[CmdletBinding()]
param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv-build"
$venvPython = Join-Path $venv "Scripts\python.exe"
$name = "CompressorArquivos"
$icon = Join-Path $root "assets\app.ico"

function Find-Iscc {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($base in @("$env:LOCALAPPDATA\Programs", ${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
        $path = Join-Path $base "Inno Setup 6\ISCC.exe"
        if (Test-Path $path) { return $path }
    }
    throw "Inno Setup 6 não encontrado. Instale com: winget install JRSoftware.InnoSetup"
}

$iscc = Find-Iscc

if (-not (Test-Path $venvPython)) {
    Write-Host "Criando ambiente de build em .venv-build..." -ForegroundColor Cyan
    $py = if (Get-Command py -ErrorAction SilentlyContinue) { @("py", "-3") } else { @("python") }
    & $py[0] @($py | Select-Object -Skip 1) -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o ambiente virtual." }
}

Write-Host "Instalando dependências..." -ForegroundColor Cyan
& $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $root "requirements.txt") pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependências." }

Write-Host "Gerando o aplicativo..." -ForegroundColor Cyan
Push-Location $root
try {
    & $venvPython -m PyInstaller `
        --noconfirm --clean --onedir --console `
        --name $name `
        --icon $icon `
        --distpath (Join-Path $root "dist") `
        --workpath (Join-Path $root "build") `
        --specpath (Join-Path $root "build") `
        --add-data "$(Join-Path $root 'app\static');app\static" `
        --collect-submodules uvicorn `
        (Join-Path $root "launcher.py")
    if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar o aplicativo." }

    Write-Host "Gerando o instalador..." -ForegroundColor Cyan
    # O antivírus às vezes ainda está analisando os arquivos recém-gerados: tenta de novo.
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        & $iscc /Q "/DAppVersion=$Version" (Join-Path $root "installer.iss")
        if ($LASTEXITCODE -eq 0) { break }
        if ($attempt -eq 3) { throw "Falha ao gerar o instalador." }
        Write-Host "  Inno Setup falhou (tentativa $attempt); tentando de novo em 5s..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
}
finally {
    Pop-Location
}

$setup = Join-Path $root "dist\$name-Setup-$Version.exe"
Write-Host ""
Write-Host "Pronto: $setup ($([math]::Round((Get-Item $setup).Length / 1MB, 1)) MB)" -ForegroundColor Green
