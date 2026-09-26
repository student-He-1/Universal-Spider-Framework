@echo off
chcp 65001 >nul
REM ============================================================
REM 爬虫一键运行脚本
REM 用法:
REM   run_spider.bat ecommerce_demo --single
REM   run_spider.bat news_demo --single
REM   run_spider.bat ecommerce_demo
REM ==========================================================

set CONDA_ROOT=D:\79458\Documents\anaconda3
set CONDA_ENV=%CONDA_ROOT%\envs\spider

REM ---- 所有数据指向 D 盘 ----
set PLAYWRIGHT_BROWSERS_PATH=D:\conda_cache\playwright
set PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
set PIP_CACHE_DIR=D:\conda_cache\pip
set PIP_CONFIG_FILE=D:\conda_cache\pip.ini
set TEMP=D:\conda_cache\temp
set TMP=D:\conda_cache\temp

call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ENV%"
cd /d "D:\桌面\爬虫"

if "%~1"=="" (
    echo 用法: run_spider.bat ^<爬虫名^> [参数]
    echo 示例: run_spider.bat ecommerce_demo --single
    exit /b 1
)

python run.py %*
