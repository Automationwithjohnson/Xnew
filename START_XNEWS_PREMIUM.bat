@echo off
title NewsMadeByAnti - X Premium Auto-Poster
cd /d "C:\Users\PRECISION\Desktop\ANTIGRAVITY\Newsmadebyanti"
echo ==================================================
echo      NEWS MADE BY ANTI - X PREMIUM AUTO-POSTER    
echo      Long-Form Mode: ENABLED (1500 chars max)
echo      Interval: Every 15 minutes
echo ==================================================
echo.
set PYTHONUNBUFFERED=1
C:\Python314\python.exe -u run_loop.py
pause
