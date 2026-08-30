import os
import sys
import time
import json
import threading
import subprocess
from collections import deque
from flask import Flask, Response

app = Flask(__name__)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_BUFFER = deque(maxlen=200)

def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_BUFFER.append(line)

def restore_cookies_if_needed():
    cookies_env = os.getenv("X_COOKIES_JSON")
    cookies_path = os.path.join(SCRIPT_DIR, "Xaccountdata.json")
    if cookies_env and len(cookies_env.strip()) > 10:
        try:
            with open(cookies_path, "w", encoding="utf-8") as f:
                f.write(cookies_env)
            log("Restored Xaccountdata.json from X_COOKIES_JSON environment variable.")
        except Exception as e:
            log(f"Failed to write cookies from env: {e}")

# Call cookie restoration at import time
restore_cookies_if_needed()

def run_process_with_logging(cmd, label):
    log(f"Starting {label} process...")
    process = subprocess.Popen(
        cmd,
        cwd=SCRIPT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    for line in iter(process.stdout.readline, ''):
        if line:
            log(f"[{label}] {line.strip()}")
    process.stdout.close()
    returncode = process.wait()
    log(f"{label} process exited with code {returncode}")

def run_news_loop():
    cmd = [sys.executable, "-u", os.path.join(SCRIPT_DIR, "run_loop.py")]
    while True:
        try:
            run_process_with_logging(cmd, "NEWS_POSTER")
        except Exception as e:
            log(f"News poster loop crashed: {e}")
        time.sleep(30)

def run_commenter_loop():
    cmd = [sys.executable, "-u", os.path.join(SCRIPT_DIR, "auto_like_comment.py")]
    while True:
        try:
            run_process_with_logging(cmd, "AUTO_COMMENTER")
        except Exception as e:
            log(f"Auto commenter loop crashed: {e}")
        time.sleep(30)

@app.route('/')
@app.route('/health')
def health_check():
    return {
        "status": "online",
        "service": "X News Poster & Auto-Commenter",
        "cookies_present": os.path.exists(os.path.join(SCRIPT_DIR, "Xaccountdata.json")),
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    }, 200

@app.route('/logs')
def view_logs():
    return Response("\n".join(LOG_BUFFER), mimetype='text/plain')

@app.route('/trigger-news')
def trigger_news():
    def _run():
        cmd = [sys.executable, "-u", os.path.join(SCRIPT_DIR, "twikit_news_poster.py"), "--limit=1", "--bypass-time-window"]
        run_process_with_logging(cmd, "MANUAL_NEWS")
    threading.Thread(target=_run, daemon=True).start()
    return {"message": "Manual news poster triggered!"}, 200

@app.route('/trigger-comment')
def trigger_comment():
    def _run():
        cmd = [sys.executable, "-u", os.path.join(SCRIPT_DIR, "auto_like_comment.py"), "--now"]
        run_process_with_logging(cmd, "MANUAL_COMMENT")
    threading.Thread(target=_run, daemon=True).start()
    return {"message": "Manual commenter triggered!"}, 200

if __name__ == "__main__":
    t1 = threading.Thread(target=run_news_loop, daemon=True)
    t2 = threading.Thread(target=run_commenter_loop, daemon=True)
    
    t1.start()
    t2.start()
    
    port = int(os.getenv("PORT", "10000"))
    log(f"Starting Flask web server on port {port}...")
    app.run(host="0.0.0.0", port=port)
