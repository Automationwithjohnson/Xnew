import json
import asyncio
import os
import twikit.user
import twikit.x_client_transaction
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

async def dummy_init(self, *args, **kwargs):
    self.key = "1234567890"
    self.key_bytes = [0] * 16

twikit.user.User.__init__ = safe_user_init
twikit.x_client_transaction.ClientTransaction.init = dummy_init
twikit.x_client_transaction.ClientTransaction.generate_transaction_id = lambda *args, **kwargs: "1234567890"

async def main():
    c = Client('en-US', user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    cookies = json.load(open('Xaccountdata.json'))
    fc = {k.get('name'): str(k.get('value')) for k in cookies if k.get('name') and k.get('value')}
    c.http.cookies.clear()
    for k, v in fc.items():
        c.http.cookies.set(k, v, domain='.x.com')
        
    print("Testing reply to tweet 2093987089226330199...")
    try:
        tweet = await c.create_tweet("Nice update!", reply_to="2093987089226330199")
        print("SUCCESS! Reply ID:", tweet.id)
    except Exception as e:
        print("ERROR posting reply:", e)

if __name__ == "__main__":
    asyncio.run(main())
