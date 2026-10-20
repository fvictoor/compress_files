; Instalador do Compressor de Arquivos (Inno Setup 6).
; Gerado pelo build-exe.ps1, que passa AppVersion. Compilar à mão:
;   ISCC.exe /DAppVersion=1.0.0 installer.iss

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Compressor de Arquivos"
#define AppExe "CompressorArquivos.exe"

[Setup]
AppId={{F0EC78C3-E20E-4CB1-AE96-8E4D3408384F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SuperaHD
; Instala só para o usuário atual: não pede senha de administrador.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=dist
OutputBaseFilename=CompressorArquivos-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\CompressorArquivos\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Logs gravados pelo launcher (%LOCALAPPDATA%\CompressorArquivos).
Type: filesandordirs; Name: "{localappdata}\CompressorArquivos"
