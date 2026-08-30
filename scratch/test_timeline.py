import json
import asyncio
from twikit import Client

async def main():
    c = Client('en-US')
    cookies = json.load(open('Xaccountdata.json'))
    fc = {k.get('name'): str(k.get('value')) for k in cookies if k.get('name') and k.get('value')}
    c.http.cookies.clear()
    for k, v in fc.items():
        c.http.cookies.set(k, v, domain='.x.com')
        
    u = await c.get_user_by_screen_name('nairametrics')
    t = await u.get_tweets('Tweets', count=5)
    print("TIMELINE SUCCESS! Found:", len(t))
    for tweet in t:
        print(f" - [{tweet.id}] {tweet.text[:60]}")

if __name__ == "__main__":
    asyncio.run(main())
