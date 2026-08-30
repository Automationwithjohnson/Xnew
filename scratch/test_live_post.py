import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import twikit_news_poster

if __name__ == "__main__":
    print("=== TRIGGERING 1 LIVE NEWS POST TO X (@AlayeCodes) ===")
    sys.argv = ["twikit_news_poster.py", "--limit=1", "--bypass-time-window"]
    asyncio.run(twikit_news_poster.main())
