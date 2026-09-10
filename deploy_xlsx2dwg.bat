@echo off
REM deploy_xlsx2dwg.bat — 在远程 worker 上执行
REM 从主 API 同步 xlsx2dwg 工具

echo === Deploy xlsx2dwg tools ===

REM 确保目录存在
if not exist "C:\opt\ACADxPDF\tools\xlsx2dwg_net\XlsxToDwg\bin\Release" (
    mkdir "C:\opt\ACADxPDF\tools\xlsx2dwg_net\XlsxToDwg\bin\Release"
)

REM 安装 openpyxl
pip install openpyxl 2>nul || python -m pip install openpyxl

REM 验证
python -c "import openpyxl; print('openpyxl OK:', openpyxl.__version__)"
echo.
echo === Done ===
echo 然后从主 API 复制以下文件到此机器:
echo   tools\xlsx2dwg_net\xlsx2json.py
echo   tools\xlsx2dwg_net\run_xlsx2dwg.py
echo   tools\xlsx2dwg_net\XlsxToDwg\bin\Release\XlsxToDwg.dll
echo   acad2pdf\xlsx2dwg_worker.py
echo.
echo 或者用 scp 从主 API 拉取:
echo   scp -r grigs@192.168.0.5:C:\opt\ACADxPDF\tools\xlsx2dwg_net C:\opt\ACADxPDF\tools\
echo   scp grigs@192.168.0.5:C:\opt\ACADxPDF\acad2pdf\xlsx2dwg_worker.py C:\opt\ACADxPDF\acad2pdf\
pause
