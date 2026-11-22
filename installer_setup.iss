; ConfeccionesR4 POS · Inno Setup 6.2+

#define MyAppName      "ConfeccionesR4 POS"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "maiwxzs"
#define MyAppExeName   "ConfeccionesR4.exe"
#define SourceDir      "dist\ConfeccionesR4"

#if !FileExists(AddBackslash(SourcePath) + SourceDir + "\" + MyAppExeName)
  #error "No existe dist\ConfeccionesR4\ConfeccionesR4.exe. Ejecute primero: python build_app.py"
#endif

#define IncluirVCRedist FileExists(AddBackslash(SourcePath) + "redist\vc_redist.x64.exe")

[Setup]
AppId={{A9D974E6-4AB9-4FA2-B6E4-FF9B82508E8F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppCopyright=© 2026 maiwxzs · Hecho para Confecciones R4
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany=Confecciones R4
VersionInfoDescription=Instalador de {#MyAppName}
DefaultDirName={autopf}\ConfeccionesR4
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
AllowNoIcons=no
OutputDir=installer_output
OutputBaseFilename=Instalador_ConfeccionesR4_v1.0
SetupIconFile=assets\logo_r4.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
WizardStyle=modern
WizardImageFile=assets\wizard_grande.bmp
WizardSmallImageFile=assets\wizard_pequeno.bmp
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no
SetupLogging=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[CustomMessages]
spanish.EjecutarAhora=Ejecutar ConfeccionesR4 POS ahora
spanish.InstalandoVC=Instalando componentes de Microsoft Visual C++ (puede tardar un minuto)...

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
Name: "{app}"; Permissions: users-modify
Name: "{commonappdata}\ConfeccionesR4"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "{#SourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\logo_r4.ico"; DestDir: "{app}"; Flags: ignoreversion
#if IncluirVCRedist
Source: "redist\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NecesitaVCRedist
#endif

[Icons]
; Grupo en el Menú Inicio
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\logo_r4.ico"; Comment: "Punto de venta e inventario"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\logo_r4.ico"; Tasks: desktopicon

[Run]
#if IncluirVCRedist
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "{cm:InstalandoVC}"; Flags: waituntilterminated runhidden; Check: NecesitaVCRedist
#endif
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:EjecutarAhora}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
Type: files; Name: "{app}\ConfeccionesR4.old.exe"

[Code]
{ Devuelve True si falta el runtime de Visual C++ 2015-2022 x64 }
function NecesitaVCRedist: Boolean;
var
  Instalado: Cardinal;
begin
  Result := True;
  if RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Instalado) then
    Result := (Instalado <> 1);
end;
