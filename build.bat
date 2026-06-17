@echo off
echo Building Ludusavi Save Adder executable...
echo.



REM Build the executable without a console window
py -m PyInstaller --noconsole --onefile --name "LudusaviSaveAdder"  ludusavi_gui.py

echo.
echo Build complete! The executable is in the "dist" folder.
pause