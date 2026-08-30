import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import twikit_news_poster

async def test():
    sys.argv = ["twikit_news_poster.py", "--limit=1", "--bypass-time-window"]
    await twikit_news_poster.main()

if __name__ == "__main__":
    asyncio.run(test())
