import json
import asyncio
from twikit import Client

TARGET_USERS = {
    "TechCrunch": "816653",
    "MKBHD": "29873662",
    "SaharaReporters": "14937748",
    "PulseNigeria": "129580459",
    "Forbes": "9120152",
    "WIRED": "1344951",
    "Verge": "27568658",
    "Engadget": "14372481"
}

async def main():
    c = Client('en-US', user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    cookies = json.load(open('Xaccountdata.json'))
    fc = {k.get('name'): str(k.get('value')) for k in cookies if k.get('name') and k.get('value')}
    c.http.cookies.clear()
    for k, v in fc.items():
        c.http.cookies.set(k, v, domain='.x.com')
        
    all_tweets = []
    for name, user_id in TARGET_USERS.items():
        try:
            tweets = await c.get_user_tweets(user_id, 'Tweets', count=5)
            if tweets:
                print(f"[{name}] SUCCESS! Found {len(tweets)} tweets")
                all_tweets.extend(tweets)
        except Exception as e:
            print(f"[{name}] Error: {e}")

    print(f"\nTOTAL TWEETS GATHERED: {len(all_tweets)}")

if __name__ == "__main__":
    asyncio.run(main())
