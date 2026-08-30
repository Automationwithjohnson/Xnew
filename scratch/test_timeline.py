import json
import asyncio
import twikit.user
from twikit import Client

_original_user_init = twikit.user.User.__init__

def safe_user_init(self, client, data: dict) -> None:
    if isinstance(data, dict):
        legacy = data.get('legacy') or {}
        data['legacy'] = legacy
        entities = legacy.get('entities') or {}
        legacy['entities'] = entities
        url_obj = entities.get('url') or {}
        entities['url'] = url_obj
    _original_user_init(self, client, data)

twikit.user.User.__init__ = safe_user_init

TARGET_USERS = {
    "Nairametrics": "14545084",
    "TechCrunch": "816653",
    "PunchNewspapers": "125740450",
    "SaharaReporters": "14937748",
    "PulseNigeria": "129580459",
    "DailyPost": "504068305",
    "BusinessDay": "41995808",
    "MKBHD": "29873662"
}

async def main():
    c = Client('en-US', user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    cookies = json.load(open('Xaccountdata.json'))
    fc = {k.get('name'): str(k.get('value')) for k in cookies if k.get('name') and k.get('value')}
    c.http.cookies.clear()
    for k, v in fc.items():
        c.http.cookies.set(k, v, domain='.x.com')
        
    if hasattr(c, 'client_transaction'):
        c.client_transaction.generate_transaction_id = lambda *args, **kwargs: "1234567890"

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
