import os
import sys
import time
import json
import subprocess
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Restore cookies from environment variable if set on Render
def restore_cookies_if_needed():
    cookies_env = os.getenv("X_COOKIES_JSON")
    cookies_path = os.path.join(SCRIPT_DIR, "Xaccountdata.json")
    if cookies_env and (not os.path.exists(cookies_path) or os.path.getsize(cookies_path) < 10):
        try:
            with open(cookies_path, "w", encoding="utf-8") as f:
                f.write(cookies_env)
            print("[Render] Restored Xaccountdata.json from X_COOKIES_JSON environment variable.")
        except Exception as e:
            print(f"[Render Warning] Failed to write cookies from env: {e}")

def run_news_loop():
    print("[Render Worker] Starting X News Poster Loop...")
    cmd = [sys.executable, "-u", os.path.join(SCRIPT_DIR, "run_loop.py")]
    subprocess.run(cmd, cwd=SCRIPT_DIR)

def run_commenter_loop():
    print("[Render Worker] Starting Auto-Like & Commenter Loop...")
    cmd = [sys.executable, "-u", os.path.join(SCRIPT_DIR, "auto_like_comment.py")]
    subprocess.run(cmd, cwd=SCRIPT_DIR)

if __name__ == "__main__":
    print("==================================================")
    print("      NEWS FOR X - RENDER UNIFIED WORKER          ")
    print("==================================================")
    
    restore_cookies_if_needed()
    
    t1 = threading.Thread(target=run_news_loop, daemon=True)
    t2 = threading.Thread(target=run_commenter_loop, daemon=True)
    
    t1.start()
    t2.start()
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("Shutting down worker threads...")
        sys.exit(0)
