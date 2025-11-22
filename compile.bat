@echo off
rem auth mwolf
rem date 20251122
rem purpose install dependencies and create .exe file

rem dependencies
pip install ftd2xx
pip install pyinstaller
rem compile to exe
pyinstaller sain_smart_ftdi_relay_control.py --onefile
echo .EXE file created successfully in the dist folder.
pause
@echo on