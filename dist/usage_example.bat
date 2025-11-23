@echo off
sainsmart_ftdi_relay_control.exe --state 1 3
timeout /T 3
sainsmart_ftdi_relay_control.exe --state 2 --momentary 4 --duration .5
timeout /T 3
sainsmart_ftdi_relay_control.exe --on 3
timeout /T 3
sainsmart_ftdi_relay_control.exe --toggle 4 2
timeout /T 3
sainsmart_ftdi_relay_control.exe --off 1 2 3 4
@echo  on