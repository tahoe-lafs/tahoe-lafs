[Setup]
AppName=Allmydata Tahoe
AppVerName=Allmydata Tahoe 2.9
AppVersion=2.9.0
VersionInfoVersion=2.9.0
AppPublisher=Allmydata Inc.
AppPublisherURL=http://www.allmydata.com/
AppSupportURL=http://www.allmydata.com/support/
DefaultDirName={pf}\Allmydata.Tahoe
DefaultGroupName=Allmydata
; minumum version NT 4, no classic windows
MinVersion=0,4.0
Compression=lzma/max
SolidCompression=yes
OutputDir=installer/Allmydata_Tahoe_Setup_v2_9_0.exe
SourceDir=dist
SetupIconFile=installer.ico
UninstallDisplayIcon=installer.ico
; license file needs to be build/all dir
;LicenseFile=../license.txt
OutputBaseFilename=AllmydataSetup-%BUILD%

[Files]
; contents of 'binaries' dir. (consolidated build target)
Source: "*.*"; DestDir: "{app}\Install"; Flags: restartreplace replacesameversion uninsrestartdelete
Source: ".\web\*.*"; DestDir: "{app}\web"; Flags: recursesubdirs

[Dirs]
Name: "{app}\noderoot"

[Icons]
; Program files entries
Name: "{group}\Tahoe root dir (web)"; Filename: "{app}\Install\tahoe.exe"; Parameters: "webopen"
Name: "{group}\Allmydata Help"; Filename: "http://www.allmydata.com/help.php"

[Run]
; Things performed before the final page of the installer
Filename: "{app}\Install\tahoesvc.exe"; Parameters: "-install"; Flags: runhidden
Filename: "{app}\Install\tahoe.exe"; Parameters: "create-client ""{app}\noderoot"""; Description: "Set the node into debug logging mode"; Flags: runhidden
Filename: "{app}\Install\confwiz.exe"; Flags: hidewizard
;Filename: "{app}\Install\ReadMe.txt"; Description: "View the ReadMe file"; Flags: unchecked postinstall nowait shellexec skipifdoesntexist

[UninstallRun]
; Performed before the uninstaller runs to undo things
Filename: "{sys}\net.exe"; Parameters: "stop Tahoe"; Flags: runhidden
Filename: "{app}\Install\tahoesvc.exe"; Parameters: "-remove"; Flags: runhidden
;Filename: "http://www.allmydata.com/redirect/uninstallsurvey.php?build=%BUILD%"; Flags: shellexec

[Registry]
Root: HKLM; Subkey: "Software\Allmydata"; Flags: uninsdeletekeyifempty
Root: HKLM; Subkey: "Software\Allmydata"; ValueType: string; ValueName: "Base Dir Path"; ValueData: "{app}\noderoot"; Flags: uninsdeletekey
