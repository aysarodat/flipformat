@echo off
pip install -r requirements.txt pyinstaller || exit /b 1
pyinstaller --noconfirm --windowed --name FlipFormat --collect-all tkinterdnd2 --collect-all pillow_heif --collect-all pillow_avif flipformat.py || exit /b 1
echo.
echo Built: dist\FlipFormat\FlipFormat.exe
echo For the installer, install Inno Setup 6, then run:
echo   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
