@echo off
REM Qurilish ERP — bazani Telegramga yuborish (Task Scheduler shu faylni ishga tushiradi)
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "C:\Users\Concept\qurilish-erp"
"C:\Users\Concept\qurilish-erp\venv\Scripts\python.exe" manage.py telegram_backup >> "C:\Users\Concept\qurilish-erp\telegram_backup.log" 2>&1
