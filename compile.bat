@echo off
rem auth mwolf
rem date 20251122
rem purpose install dependencies and create .exe file

rem dependencies
pip install ftd2xx
pip install pyinstaller
rem compile to exe
pyinstaller sainsmart_ftdi_relay_control.py --onefile
copy "ftd2xx.dll" "dist/ftd2xx.dll"
echo .EXE file created successfully in the dist folder.
pause
@echo on