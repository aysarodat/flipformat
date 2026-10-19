@echo off
REM Builds dist\FlipFormat\FlipFormat.exe (no Python needed to run it).
pip install -r requirements.txt pyinstaller || exit /b 1
if not exist models mkdir models
if not exist models\u2net_human_seg.onnx (
  echo Downloading the background-removal model, about 176 MB...
  curl -L -o models\u2net_human_seg.onnx https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx || exit /b 1
)
pyinstaller --noconfirm --windowed --name FlipFormat --icon assets\flipformat.ico ^
  --add-data "assets;assets" --add-data "models;models" ^
  --collect-all tkinterdnd2 --collect-all pillow_heif --collect-all pillow_avif --collect-all onnxruntime ^
  flipformat.py || exit /b 1
echo.
echo Built: dist\FlipFormat\FlipFormat.exe
echo For the installer, install Inno Setup 6, then run:
echo   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
