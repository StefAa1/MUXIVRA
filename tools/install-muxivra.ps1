# MUXIVRA — εγκατάσταση χωρίς installer (PowerShell)
#
# Χρήση (PowerShell, όχι cmd):
#   powershell -ExecutionPolicy Bypass -File install-muxivra.ps1
#
# Τι κάνει: κατεβάζει το τελευταίο signed release, επαληθεύει το SHA-256 με το
# release.json, το τοποθετεί στο %LOCALAPPDATA%\MUXIVRA και φτιάχνει shortcuts.
# Δεν αγγίζει το data\ (πελάτες, vault, hosts) — αναβαθμίζει μόνο τα binaries.

param(
    [string]$Repo = "StefAa1/MUXIVRA",
    [string]$Dest = "$env:LOCALAPPDATA\MUXIVRA"
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host "→ Αναζήτηση τελευταίας έκδοσης ($Repo)…"
$release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
$tag = $release.tag_name
$zipAsset = $release.assets | Where-Object { $_.name -eq "MUXIVRA-portable-win64.zip" }
$manAsset = $release.assets | Where-Object { $_.name -eq "release.json" }
if (-not $zipAsset -or -not $manAsset) { throw "Το release $tag δεν έχει τα απαιτούμενα assets." }

$tmp = Join-Path $env:TEMP ("muxivra-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
$zip = Join-Path $tmp "MUXIVRA.zip"
$man = Join-Path $tmp "release.json"

Write-Host "→ Λήψη $tag…"
Invoke-WebRequest $zipAsset.browser_download_url -OutFile $zip -UseBasicParsing
Invoke-WebRequest $manAsset.browser_download_url -OutFile $man -UseBasicParsing

Write-Host "→ Έλεγχος SHA-256…"
$expected = (Get-Content $man -Raw | ConvertFrom-Json).sha256
$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($expected -ne $actual) {
    throw "SHA-256 δεν ταιριάζει: expected $expected, got $actual. Δεν έγινε εγκατάσταση."
}
Write-Host "  OK $actual"

Write-Host "→ Αποσυμπίεση…"
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$src = Join-Path $tmp "MUXIVRA-portable-win64"
if (-not (Test-Path (Join-Path $src "MUXIVRA.exe"))) { throw "Το ZIP δεν περιέχει MUXIVRA.exe" }

New-Item -ItemType Directory -Path $Dest -Force | Out-Null
Write-Host "→ Αντιγραφή στο $Dest…"
Copy-Item (Join-Path $src "*") $Dest -Recurse -Force

Write-Host "→ Συντομεύσεις…"
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "MUXIVRA.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "MUXIVRA.lnk")
)
foreach ($lnk in $targets) {
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = Join-Path $Dest "MUXIVRA.exe"
    $sc.WorkingDirectory = $Dest
    $sc.IconLocation = (Join-Path $Dest "MUXIVRA.exe") + ",0"
    $sc.Save()
}

Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "✔ Εγκαταστάθηκε το $tag στο $Dest"
Write-Host "  Εκκίνηση: $Dest\MUXIVRA.exe  (τα δεδομένα μένουν στο $Dest\data)"
