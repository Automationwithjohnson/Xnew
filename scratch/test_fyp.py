import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import auto_like_comment

async def test_fyp():
    print("=== TESTING FYP / HOME TIMELINE FETCHING ===")
    client = await auto_like_comment.setup_twitter_client()
    
    try:
        # Fetch Home Timeline / FYP tweets
        timeline_tweets = await client.get_timeline(count=20)
        print(f"Successfully fetched {len(timeline_tweets)} tweets from FYP / Home Timeline!")
        for i, tweet in enumerate(timeline_tweets[:10], 1):
            author = tweet.user.screen_name if tweet.user else 'Unknown'
            text_snippet = tweet.text[:80].replace('\n', ' ')
            print(f" {i}. [@{author}] (ID: {tweet.id}): {text_snippet}...")
    except Exception as e:
        print(f"Failed to fetch FYP timeline: {e}")

if __name__ == "__main__":
    asyncio.run(test_fyp())
