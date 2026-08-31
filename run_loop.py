import os
import sys
import time
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
POSTER_SCRIPT = os.path.join(SCRIPT_DIR, "twikit_news_poster.py")
INTERVAL_SECONDS = 120  # 2 minutes (continuously checks 5-minute window)

def run_poster():
    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Triggering news poster run...")
    args = [sys.executable, "-u", POSTER_SCRIPT] + sys.argv[1:]
    
    try:
        result = subprocess.run(args, cwd=SCRIPT_DIR, check=True)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Run completed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Error during run: Poster script exited with code {e.returncode}")
    except Exception as e:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Failed to execute poster script: {e}")

def main():
    print("==================================================")
    print("     NEWS MADE BY ANTI - RUNNER LOOP SCHEDULER    ")
    print(f"   Interval: Every {INTERVAL_SECONDS / 60:.1f} minutes")
    print("   Press Ctrl+C to terminate the loop")
    print("==================================================")
    
    # Run immediately on start
    run_poster()
    
    try:
        while True:
            print(f"\nSleeping for {INTERVAL_SECONDS / 60:.1f} minutes...")
            time.sleep(INTERVAL_SECONDS)
            run_poster()
    except KeyboardInterrupt:
        print("\nRunner loop terminated by user. Goodbye!")
        sys.exit(0)

if __name__ == "__main__":
    main()
