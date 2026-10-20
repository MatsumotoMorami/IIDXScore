@echo off
chcp 65001 >nul
"%~dp0IIDXScoreAutoMigrate.exe" --root "%~dp0"
echo.
pause
