@echo off
chcp 65001 >nul
REM ============================================================
REM 爬虫框架环境激活脚本
REM 激活 conda spider 环境，所有缓存/临时/浏览器数据全部指向 D 盘
REM ==========================================================

REM ---- conda 环境路径 ----
set CONDA_ROOT=D:\79458\Documents\anaconda3
set CONDA_ENV=%CONDA_ROOT%\envs\spider

REM ---- 所有缓存/临时数据全部放 D 盘，禁止写 C 盘 ----
set PLAYWRIGHT_BROWSERS_PATH=D:\conda_cache\playwright
set PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
set PIP_CACHE_DIR=D:\conda_cache\pip
set PIP_CONFIG_FILE=D:\conda_cache\pip.ini
set TEMP=D:\conda_cache\temp
set TMP=D:\conda_cache\temp

REM ---- 激活 conda 环境 ----
call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ENV%"

echo.
echo ============================================================
echo  爬虫环境已激活 [spider / Python 3.11]
echo ============================================================
echo  Python      : %CONDA_ENV%\python.exe
echo  浏览器      : %PLAYWRIGHT_BROWSERS_PATH%
echo  pip缓存     : %PIP_CACHE_DIR%
echo  临时目录    : %TEMP%
echo  项目目录    : D:\桌面\爬虫
echo ------------------------------------------------------------
echo  运行示例:
echo    python run.py ecommerce_demo --single   (单机,电商)
echo    python run.py news_demo --single        (单机,新闻)
echo    python run.py ecommerce_demo            (分布式,需Redis)
echo    python scripts\verify_env.py                (环境自检)
echo ============================================================
echo.

cmd /k
