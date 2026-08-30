@echo off
title Twitter Auto Like & Comment Bot (Immediate Run)
cd /d "C:\Users\PRECISION\Desktop\ANTIGRAVITY\Newsmadebyanti"
echo ==================================================
echo      TWITTER AUTO LIKE & COMMENT BOT (--now)     
echo      Mode: Manual Immediate Run (5 replies)
echo      AI Model: Gemini 2.5 Vision / Nemotron
echo ==================================================
echo.
set PYTHONUNBUFFERED=1
C:\Python314\python.exe -u auto_like_comment.py --now
echo.
echo Process complete. Press any key to close.
pause
