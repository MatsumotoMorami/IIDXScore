param([string]$Python = 'python')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot
Set-Location $root
& $Python -m venv build/exe-venv
if($LASTEXITCODE){throw 'venv failed'}
$runtime=Join-Path $root 'build/exe-venv/Scripts/python.exe'
& $runtime -m pip install 'pyinstaller==6.22.3'
if($LASTEXITCODE){throw 'PyInstaller install failed'}
& $runtime -m PyInstaller --noconfirm --clean --onefile --windowed --name IIDXScoreImport --distpath dist --workpath build/pyinstaller --specpath build tools/import_gui.py
if($LASTEXITCODE){throw 'EXE build failed'}
