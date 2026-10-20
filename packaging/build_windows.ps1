# Build dist\SpiralConcentrator.exe on Windows.
# Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

python -m pip install --upgrade "pyinstaller>=6.10,<7"
python packaging\make_icon.py
python -m PyInstaller --noconfirm --clean packaging\SpiralConcentrator.spec

$Exe = Join-Path $Root "dist\SpiralConcentrator.exe"
if (-not (Test-Path $Exe)) {
    throw "PyInstaller did not write $Exe"
}
Write-Host "Built $Exe"
Write-Host "Double-click that file, or run: dist\SpiralConcentrator.exe --web"
