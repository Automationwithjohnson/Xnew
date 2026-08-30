@echo off
title Twitter Auto Like & Comment Bot (30-Min Loop)
cd /d "C:\Users\PRECISION\Desktop\ANTIGRAVITY\Newsmadebyanti"
echo ==================================================
echo      TWITTER AUTO LIKE & COMMENT BOT (30m Loop)  
echo      Mode: Continuous Background Loop (Every 30m)
echo      AI Model: Gemini 2.5 Vision / Nemotron
echo ==================================================
echo.
set PYTHONUNBUFFERED=1
C:\Python314\python.exe -u auto_like_comment.py
pause
