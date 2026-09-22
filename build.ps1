param([Parameter(Mandatory=$true)][string]$ZigPath)
$ErrorActionPreference='Stop'
$ZigPath=(Resolve-Path -LiteralPath $ZigPath).Path
$root=$PSScriptRoot
$out=Join-Path $root 'build'
New-Item -ItemType Directory -Path $out -Force | Out-Null
& $ZigPath cc "$root/vendor/sqlite/sqlite3.c" -c -O2 -target x86_64-windows-gnu -DSQLITE_THREADSAFE=1 -DSQLITE_OMIT_LOAD_EXTENSION -o "$out/sqlite3.o"
if($LASTEXITCODE){throw 'SQLite compilation failed'}
& $ZigPath c++ "$root/src/plugin.cpp" "$out/sqlite3.o" "-I$root/vendor/sqlite" -shared -O2 -static -std=c++17 -target x86_64-windows-gnu -lbcrypt -o "$out/IIDXScore.dll"
if($LASTEXITCODE){throw 'DLL compilation failed'}
& $ZigPath c++ "$root/tests/test.cpp" "$out/sqlite3.o" "-I$root/vendor/sqlite" -O2 -static -std=c++17 -target x86_64-windows-gnu -lbcrypt -o "$out/test-score.exe"
if($LASTEXITCODE){throw 'Test compilation failed'}
Copy-Item -LiteralPath "$root/IIDXScore.ini" -Destination "$out/IIDXScore.ini" -Force
