import os
import sys
import time
import json
import threading
import subprocess
from flask import Flask

app = Flask(__name__)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def restore_cookies_if_needed():
    cookies_env = os.getenv("X_COOKIES_JSON")
    cookies_path = os.path.join(SCRIPT_DIR, "Xaccountdata.json")
    if cookies_env and (not os.path.exists(cookies_path) or os.path.getsize(cookies_path) < 10):
        try:
            with open(cookies_path, "w", encoding="utf-8") as f:
                f.write(cookies_env)
            print("[Render Server] Restored Xaccountdata.json from X_COOKIES_JSON environment variable.")
        except Exception as e:
            print(f"[Render Server Warning] Failed to write cookies from env: {e}")

def run_news_loop():
    print("[Render Worker] Starting X News Poster Loop...")
    cmd = [sys.executable, "-u", os.path.join(SCRIPT_DIR, "run_loop.py")]
    subprocess.run(cmd, cwd=SCRIPT_DIR)

def run_commenter_loop():
    print("[Render Worker] Starting Auto-Like & Commenter Loop...")
    cmd = [sys.executable, "-u", os.path.join(SCRIPT_DIR, "auto_like_comment.py")]
    subprocess.run(cmd, cwd=SCRIPT_DIR)

@app.route('/')
@app.route('/health')
def health_check():
    return {
        "status": "online",
        "service": "X News Poster & Auto-Commenter",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    }, 200

if __name__ == "__main__":
    restore_cookies_if_needed()
    
    t1 = threading.Thread(target=run_news_loop, daemon=True)
    t2 = threading.Thread(target=run_commenter_loop, daemon=True)
    
    t1.start()
    t2.start()
    
    port = int(os.getenv("PORT", "10000"))
    print(f"[Render Server] Starting Flask web server on port {port}...")
    app.run(host="0.0.0.0", port=port)
