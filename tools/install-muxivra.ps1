# MUXIVRA - install without the NSIS setup (PowerShell)
#
# Run (PowerShell, not cmd):
#   powershell -ExecutionPolicy Bypass -File install-muxivra.ps1
#
# What it does: downloads the latest signed release, verifies the SHA-256
# against the signed release.json, extracts it into %LOCALAPPDATA%\MUXIVRA and
# creates shortcuts. It never touches data\ (customers, vault, hosts).
# ASCII only on purpose: PowerShell 5.1 reads BOM-less files as ANSI.

param(
    [string]$Repo = "StefAa1/MUXIVRA",
    [string]$Dest = "$env:LOCALAPPDATA\MUXIVRA"
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host "-> Looking up the latest release ($Repo)"
$release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
$tag = $release.tag_name
$zipAsset = $release.assets | Where-Object { $_.name -eq "MUXIVRA-portable-win64.zip" }
$manAsset = $release.assets | Where-Object { $_.name -eq "release.json" }
if (-not $zipAsset -or -not $manAsset) { throw "Release $tag is missing the required assets." }

$tmp = Join-Path $env:TEMP ("muxivra-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
$zip = Join-Path $tmp "MUXIVRA.zip"
$man = Join-Path $tmp "release.json"

Write-Host "-> Downloading $tag"
Invoke-WebRequest $zipAsset.browser_download_url -OutFile $zip -UseBasicParsing
Invoke-WebRequest $manAsset.browser_download_url -OutFile $man -UseBasicParsing

Write-Host "-> Checking SHA-256"
$expected = (Get-Content $man -Raw | ConvertFrom-Json).sha256
$actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($expected -ne $actual) {
    throw "SHA-256 mismatch: expected $expected, got $actual. Nothing was installed."
}
Write-Host "   OK $actual"

Write-Host "-> Extracting"
Expand-Archive -Path $zip -DestinationPath $tmp -Force
$src = Join-Path $tmp "MUXIVRA-portable-win64"
if (-not (Test-Path (Join-Path $src "MUXIVRA.exe"))) { throw "The ZIP does not contain MUXIVRA.exe" }

New-Item -ItemType Directory -Path $Dest -Force | Out-Null
Write-Host "-> Copying to $Dest"
Copy-Item (Join-Path $src "*") $Dest -Recurse -Force

Write-Host "-> Creating shortcuts"
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
Write-Host "Done: $tag installed in $Dest"
Write-Host "Start it with: $Dest\MUXIVRA.exe   (your data stays in $Dest\data)"
