import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import twikit_news_poster

async def test():
    client = await twikit_news_poster.setup_twitter_client()
    db_conn = twikit_news_poster.setup_database()
    
    article = {
        "title": "Digitvant Pay 2.0 Offers Up to 25% Interest Rate",
        "link": "https://nairametrics.com/2026/08/26/your-savings-could-be-earning-more-digitvant-pay-2-0-offers-up-to-25-interest/",
        "category": "Business",
        "source": "Nairametrics",
        "author": "NM Partners",
        "description": "Digitvant Pay 2.0 offers high interest rates on savings."
    }
    
    posted = await twikit_news_poster.process_post(client, db_conn, article, dry_run=False)
    print("FINAL POST RESULT:", posted)

if __name__ == "__main__":
    asyncio.run(test())
