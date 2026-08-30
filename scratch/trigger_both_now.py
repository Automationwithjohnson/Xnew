import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import twikit_news_poster
import auto_like_comment

async def run_both():
    print("==================================================")
    print("  TRIGGERING NEWS POST & FYP COMMENT TO TWITTER   ")
    print("==================================================")
    
    # 1. NEWS POST
    print("\n--- [1] EXECUTING NEWS POST ---")
    try:
        sys.argv = ["twikit_news_poster.py", "--limit=1", "--bypass-time-window"]
        await twikit_news_poster.main()
    except Exception as e:
        print(f"News post execution error: {e}")
        
    # 2. FYP COMMENT
    print("\n--- [2] EXECUTING FYP COMMENT ---")
    try:
        sys.argv = ["auto_like_comment.py", "--now"]
        await auto_like_comment.main()
    except Exception as e:
        print(f"FYP commenter execution error: {e}")

if __name__ == "__main__":
    asyncio.run(run_both())
