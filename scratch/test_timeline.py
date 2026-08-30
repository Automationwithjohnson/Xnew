import json
import asyncio
from twikit import Client

async def main():
    c = Client('en-US', user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    cookies = json.load(open('Xaccountdata.json'))
    fc = {k.get('name'): str(k.get('value')) for k in cookies if k.get('name') and k.get('value')}
    c.http.cookies.clear()
    for k, v in fc.items():
        c.http.cookies.set(k, v, domain='.x.com')
        
    # Disable client_transaction key fetching if blocked
    if hasattr(c, 'client_transaction'):
        c.client_transaction.generate_transaction_id = lambda *args, **kwargs: "1234567890"
        
    u = await c.get_user_by_screen_name('nairametrics')
    t = await u.get_tweets('Tweets', count=5)
    print("PATCH SUCCESSFUL! Found:", len(t))
    for tweet in t:
        print(f" - [{tweet.id}] {tweet.text[:60]}")

if __name__ == "__main__":
    asyncio.run(main())
