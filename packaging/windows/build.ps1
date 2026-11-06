# Build the Windows installer by hand on a Windows 10/11 PC.
# (GitHub Actions does exactly this automatically on every version tag.)
#
# Requirements: Python 3.12 (python.org, 64-bit) and Inno Setup 6
#   winget install Python.Python.3.12
#   winget install JRSoftware.InnoSetup
#
# Run from the repository root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..\..')

$version = (Select-String -Path download_manager_gui.py -Pattern "^APP_VERSION = '(.+)'").Matches[0].Groups[1].Value
Write-Host "==> Building Macra Downloader $version"

py -3.12 -m venv .venv-win
.\.venv-win\Scripts\python -m pip install --upgrade pip
.\.venv-win\Scripts\pip install -r requirements.txt pyinstaller

Write-Host '==> Running tests'
.\.venv-win\Scripts\python -m unittest test_download_manager

Write-Host '==> PyInstaller'
.\.venv-win\Scripts\pyinstaller --noconfirm --clean packaging\windows\macra-downloader.spec

Write-Host '==> Inno Setup'
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 not found (winget install JRSoftware.InnoSetup)' }
& $iscc "/DAppVersion=$version" packaging\windows\installer.iss

Write-Host "==> Done: Output\MacraDownloader-Setup-x64.exe"
