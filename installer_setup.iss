; =============================================================================
;  installer_setup.iss — Instalador de ConfeccionesR4 POS (Inno Setup 6.2+)
;
;  Requisito: haber ejecutado antes  `python build_app.py`
;             (genera UN solo archivo: dist\ConfeccionesR4\ConfeccionesR4.exe).
;  Opcional:  `python build_app.py --vcredist` deja redist\vc_redist.x64.exe y el
;             instalador lo ejecuta en silencio SOLO si el PC no tiene el runtime de C++.
;
;  Compilar:  ISCC.exe installer_setup.iss   (o abrir en Inno Setup › Build › Compile)
;  Resultado: installer_output\Instalador_ConfeccionesR4_v1.0.exe
; =============================================================================

#define MyAppName      "ConfeccionesR4 POS"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"   ; GitHub Actions lo pasa con /DMyAppVersion=1.0.<n>
#endif
#define MyAppPublisher "CONFECCIONESR4"
#define MyAppExeName   "ConfeccionesR4.exe"
#define MyAppURL       "https://wa.me/573028694690"
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
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppCopyright=CONFECCIONESR4 · NIT 700571557-9 · Bogotá D.C.
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
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
; Marcado por defecto: el usuario solo pulsa "Siguiente"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Dirs]
; Permiso de modificación para usuarios estándar:
;  - {app}: las actualizaciones remotas reemplazan el .exe sin pedir administrador
;  - ProgramData: base de datos compartida por todos los usuarios de Windows del equipo
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
; Acceso directo en el Escritorio con el ícono corporativo
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\logo_r4.ico"; Tasks: desktopicon

[Run]
#if IncluirVCRedist
; Dependencia runtime instalada en silencio, sin intervención del operador
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "{cm:InstalandoVC}"; Flags: waituntilterminated runhidden; Check: NecesitaVCRedist
#endif
; Casilla final:  [X] Ejecutar ConfeccionesR4 POS ahora
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:EjecutarAhora}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
; Solo archivos del programa. Las ventas y el inventario (C:\ProgramData\ConfeccionesR4) NO se borran.
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
