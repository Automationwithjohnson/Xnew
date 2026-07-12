import os
import sys
import time
import sqlite3
import random
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from twikit import Client

# Reconfigure stdout/stderr to use UTF-8 to prevent console crashes
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

# Load environment variables
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
COOKIES_PATH = os.getenv("COOKIES_PATH", "Xaccountdata.json")
DB_PATH = "auto_replied.db"

# Settings
LOOP_INTERVAL_MINUTES = 10
MAX_REPLIES_PER_BATCH = 3

if not API_KEY:
    print("Error: OPENROUTER_API_KEY is not set in .env file!")
    sys.exit(1)

def setup_database():
    conn = sqlite3.connect(os.path.join(SCRIPT_DIR, DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS replied (
            tweet_id TEXT UNIQUE,
            replied_at REAL
        )
    """)
    conn.commit()
    return conn

def is_already_replied(conn, tweet_id):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM replied WHERE tweet_id = ?", (tweet_id,))
    return cursor.fetchone() is not None

def record_reply(conn, tweet_id):
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO replied (tweet_id, replied_at) VALUES (?, ?)", (tweet_id, time.time()))
    conn.commit()

def call_openrouter(x_post_text):
    max_length = int(os.getenv("MAX_TWEET_LENGTH", "600"))
    ai_limit = max_length - 60
    
    prompt = f"""You are a sharp, street-smart Nigerian commentator with deep knowledge of Nigerian and African news, tech, business, politics, and security. You write in a natural, confident, and insightful way. Write like a well-informed person sharing their honest take.

Task:
I will give you an X post from my timeline. You will analyze it and write a short, engaging reply to post under it.

Rules:

- Length Constraint: The entire reply MUST be under {ai_limit} characters. Keep it brief.
- Simple English Constraint: Write in very simple English that even a kid can understand.
- Punctuation Constraint: Do not use em dashes (—) or en dashes (–) anywhere. ONLY use standard commas (,) and periods/full stops (.) for punctuation. Do not use exclamation marks (!), question marks (?), colons (:), semicolons (;), or dashes anywhere in your text. If you ask a question at the end, end it with a period. If a sentence requires a pause, use conjunctions (and, but, so) or split it into two distinct sentences. Maintain a clean, direct sentence structure.
- Add real value: Give insight, context, implication, or a unique angle. Never just agree or repeat the post.
- Style Variation: Vary your opening style. Sometimes start with a direct question, sometimes with a strong opinion, and sometimes with a surprising fact. Make each response feel unique, fresh, and distinct.
- Natural Angle: Only bring in the Nigerian or broader African perspective when it naturally fits the post. Do not force it on every single post if it feels out of place.
- Sound natural and conversational (not robotic or corporate).
- Keep it relatively short and easy to read on mobile.
- End with a thoughtful question when it makes sense (end it with a period instead of a question mark).
- Be confident but respectful. Avoid being overly aggressive or controversial unless the original post is clearly one-sided.
- Match the energy and topic of the original post (serious for serious topics, lighter where appropriate).
- Do not use any punctuation marks other than standard periods and commas.

Output Format (Follow this exactly):
Output ONLY the reply text. Do not include any headers, labels, or intros.

Here is the X post:
{x_post_text}"""

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://automatewithjohnson.com",
        "X-Title": "AutomatesWithJohnson Auto Replier"
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=40)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip().replace('"', '')
    except Exception as e:
        print(f"OpenRouter call failed: {e}")
        return None

async def setup_twitter_client():
    client = Client("en-US")
    if not os.path.exists(os.path.join(SCRIPT_DIR, COOKIES_PATH)):
        raise Exception(f"Cookies file not found at {COOKIES_PATH}")
    client.load_cookies(os.path.join(SCRIPT_DIR, COOKIES_PATH))
    return client

async def run_replier_batch():
    db_conn = setup_database()
    
    try:
        client = await setup_twitter_client()
        user_info = await client.user()
        my_id = user_info.id
        my_username = user_info.screen_name
        print(f"Logged in as @{my_username} (ID: {my_id})")
    except Exception as e:
        print(f"Twitter login failed: {e}")
        db_conn.close()
        return

    print("Fetching home timeline...")
    try:
        tweets = await client.get_timeline(count=20)
    except Exception as e:
        print(f"Failed to fetch timeline: {e}")
        db_conn.close()
        return

    unreplied_tweets = []
    for tweet in tweets:
        # 1. Skip if it is our own tweet
        if tweet.user.id == my_id or tweet.user.screen_name == my_username:
            continue
            
        # 2. Skip if we already replied to it
        if is_already_replied(db_conn, tweet.id):
            continue
            
        # 3. Skip if it is a retweet (retweeted_status exists)
        if hasattr(tweet, "retweeted_status") and tweet.retweeted_status:
            continue
            
        unreplied_tweets.append(tweet)

    print(f"Found {len(unreplied_tweets)} potential tweets to reply to.")
    if not unreplied_tweets:
        db_conn.close()
        return

    # Take a batch of top N tweets to process
    to_reply = unreplied_tweets[:MAX_REPLIES_PER_BATCH]
    print(f"Processing up to {len(to_reply)} replies in this batch...")

    for idx, tweet in enumerate(to_reply):
        print(f"\nAnalyzing tweet from @{tweet.user.screen_name}: {tweet.text[:50]}...")
        
        reply_content = call_openrouter(tweet.text)
        if not reply_content:
            print("Failed to generate reply. Skipping.")
            continue
            
        print(f"Drafted Reply:\n{reply_content}")
        
        try:
            print(f"Posting reply to X...")
            await client.create_tweet(text=reply_content, reply_to=tweet.id)
            print("Reply posted successfully!")
            record_reply(db_conn, tweet.id)
        except Exception as e:
            print(f"Failed to post reply: {e}")
            # Still record to avoid infinite loop crashes on broken tweets
            record_reply(db_conn, tweet.id)

        # Wait random delay between replies in the batch (2 to 4 minutes)
        if idx < len(to_reply) - 1:
            delay = random.randint(120, 240)
            print(f"Sleeping for {delay / 60:.1f} minutes before next reply...")
            await asyncio.sleep(delay)

    db_conn.close()

async def main():
    print("==================================================")
    print("       AUTO REPLIER FOR X TIMELINE STARTING        ")
    print(f"   Interval: Every {LOOP_INTERVAL_MINUTES} minutes")
    print("   Press Ctrl+C to terminate the loop")
    print("==================================================")
    
    while True:
        try:
            print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Triggering timeline reply run...")
            await run_replier_batch()
        except Exception as e:
            print(f"Error during execution batch: {e}")
            
        print(f"Sleeping for {LOOP_INTERVAL_MINUTES} minutes...")
        await asyncio.sleep(LOOP_INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAuto-replier stopped. Goodbye!")
