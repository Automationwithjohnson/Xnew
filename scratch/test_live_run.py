import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import asyncio
import re
import twikit.user
import twikit.x_client_transaction
from twikit import Client
from auto_like_comment import call_openrouter, setup_twitter_client, deduplicate_cookies

async def main():
    print("Initializing client...")
    c = await setup_twitter_client()
    deduplicate_cookies(c)
    
    print("Fetching timeline for TechCrunch...")
    tweets = await c.get_user_tweets("816653", "Tweets", count=5)
    print(f"Found {len(tweets)} tweets.")
    
    for tweet in tweets:
        print(f"\nTweet ID: {tweet.id}")
        print(f"Text: {tweet.text[:100]}...")
        comment = call_openrouter(tweet.text)
        print("Generated Comment:", comment)
        if comment:
            print("Attempting to post live reply to tweet", tweet.id, "...")
            try:
                reply_tweet = await c.create_tweet(comment, reply_to=tweet.id)
                print("SUCCESS! Posted Reply ID:", reply_tweet.id)
                break
            except Exception as e:
                print("Failed to post reply:", e)

if __name__ == "__main__":
    asyncio.run(main())
