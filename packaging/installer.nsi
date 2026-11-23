; MUXIVRA — Windows installer (per-user, no admin required)
; Installs the portable bundle into %LOCALAPPDATA%\MUXIVRA and leaves user data
; in %LOCALAPPDATA%\MUXIVRA\data untouched across upgrades and uninstall.

Unicode true
!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef BUNDLE_DIR
  !define BUNDLE_DIR "..\dist\MUXIVRA-portable-win64"
!endif
!ifndef OUT_FILE
  !define OUT_FILE "..\dist\MUXIVRA-Setup.exe"
!endif

Name "MUXIVRA"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\MUXIVRA"
InstallDirRegKey HKCU "Software\MUXIVRA" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "MUXIVRA ${VERSION}"

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "MUXIVRA"
VIAddVersionKey "FileDescription" "Network workspace installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "MUXIVRA"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\MUXIVRA.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Open MUXIVRA"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"


; ---------------------------------------------------------------- running app
; Κλείνουμε ΜΟΝΟ τη δική μας διεργασία: πρώτα από το pid του instance.lock
; (γράφεται χωρίς newline), αλλιώς από τον τίτλο παραθύρου. Ποτέ σφαιρικό kill.
!macro StopOurApp
  DetailPrint "Stopping a running MUXIVRA instance (ours only)..."
  StrCpy $1 ""
  IfFileExists "$LOCALAPPDATA\MUXIVRA\data\instance.lock" 0 stop_by_title
    FileOpen $0 "$LOCALAPPDATA\MUXIVRA\data\instance.lock" r
    FileRead $0 $1
    FileClose $0
  stop_by_title:
  StrCmp $1 "" 0 +3
    nsExec::ExecToLog 'taskkill /FI "WINDOWTITLE eq MUXIVRA*" /F'
    Goto stop_wait
  DetailPrint "  closing PID $1"
  nsExec::ExecToLog 'taskkill /PID $1 /F'
  nsExec::ExecToLog 'taskkill /FI "WINDOWTITLE eq MUXIVRA*" /F'
  stop_wait:
  Sleep 1200
!macroend

Section "MUXIVRA" SecMain
  SetOutPath "$INSTDIR"
  !insertmacro StopOurApp
  ; Αν ένα αρχείο είναι ακόμη κλειδωμένο (AV/scan ή zombie process), το NSIS
  ; σταματούσε με «Error opening file for writing» και ο wizard έκλεινε.
  ; Με 'try' συνεχίζει, κρατά το παλιό αρχείο και το σημειώνει για reboot.
  SetOverwrite try
  ClearErrors
  ; keep user data: the bundle's data\ folder is never shipped or overwritten
  File /r "${BUNDLE_DIR}\*.*"
  IfErrors 0 +3
    SetRebootFlag true
    MessageBox MB_ICONEXCLAMATION|MB_OK "Some files were in use and could not be replaced.\
      \n\nThe update is installed and will finish after a restart."

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\MUXIVRA" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\MUXIVRA" "Version" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MUXIVRA" "DisplayName" "MUXIVRA"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MUXIVRA" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MUXIVRA" "DisplayIcon" "$INSTDIR\MUXIVRA.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MUXIVRA" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MUXIVRA" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MUXIVRA" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MUXIVRA" "EstimatedSize" "$0"

  CreateDirectory "$SMPROGRAMS\MUXIVRA"
  CreateShortcut "$SMPROGRAMS\MUXIVRA\MUXIVRA.lnk" "$INSTDIR\MUXIVRA.exe" "" "$INSTDIR\MUXIVRA.exe" 0
  CreateShortcut "$SMPROGRAMS\MUXIVRA\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\MUXIVRA.lnk" "$INSTDIR\MUXIVRA.exe" "" "$INSTDIR\MUXIVRA.exe" 0
SectionEnd

Section "Uninstall"
  !insertmacro StopOurApp
  Delete "$DESKTOP\MUXIVRA.lnk"
  Delete "$SMPROGRAMS\MUXIVRA\MUXIVRA.lnk"
  Delete "$SMPROGRAMS\MUXIVRA\Uninstall.lnk"
  RMDir "$SMPROGRAMS\MUXIVRA"
  ; remove program files but keep the user's data folder
  Delete "$INSTDIR\MUXIVRA.exe"
  Delete "$INSTDIR\MUXIVRA.cmd"
  Delete "$INSTDIR\MUXIVRA-web.cmd"
  Delete "$INSTDIR\MUXIVRA-debug.cmd"
  Delete "$INSTDIR\README-WINDOWS.txt"
  Delete "$INSTDIR\NATIVE-TESTS.md"
  Delete "$INSTDIR\BUILD.txt"
  Delete "$INSTDIR\update-pubkey.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MUXIVRA"
  DeleteRegKey HKCU "Software\MUXIVRA"
  MessageBox MB_ICONINFORMATION "Ο φάκελος δεδομένων σου έμεινε στο %LOCALAPPDATA%\MUXIVRA\data (πελάτες, vault, τεκμηρίωση). Διάγραψέ τον μόνο αν θέλεις να χαθούν."
SectionEnd
