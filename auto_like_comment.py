import os
import sys
import time
import sqlite3
import random
import json
import asyncio
import requests
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from twikit import Client
import twikit.user
import twikit.x_client_transaction

import twikit_patches  # Shared twikit compatibility patches (safe_user_init + ClientTransaction bypass)

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
MODEL = os.getenv("OPENROUTER_MODEL", "inclusionai/ling-3.0-flash-vl")
COOKIES_PATH = os.getenv("COOKIES_PATH", "Xaccountdata.json")
DB_PATH = "liked_comments.db"

# Target configuration
LOOP_INTERVAL_MINUTES = 6

# 10 Dynamic Buyer Intent Rotation Buckets targeting business owners
BUYER_ROTATION_BUCKETS = [
    # Bucket 1: Zapier & Make Bill Shock (Fastest Migration Deals)
    '(zapier expensive OR "zapier pricing" OR "zapier bill" OR "switch from zapier" OR "zapier alternative" OR "zapier task limit" OR "make.com operations") lang:en -giveaway -crypto -nigeria -filter:retweets',
    # Bucket 2: Agency & B2B Lead Scraping / Enrichment ($1k–$3k deals)
    '("scrape leads" OR "lead enrichment workflow" OR "clay table" OR "apollo scrape" OR "cold email workflow" OR "enriching leads") min_faves:1 lang:en -crypto -nigeria -filter:retweets',
    # Bucket 3: CRM Synchronization & Data Entry Nightmares
    '("manual data entry" OR "sync hubspot" OR "notion crm sync" OR "airtable webhook" OR "crm sync broken" OR "copying data between") lang:en -giveaway -nigeria -filter:retweets',
    # Bucket 4: Client Onboarding & Invoicing Bottlenecks
    '("automate onboarding" OR "client onboarding workflow" OR "stripe invoice workflow" OR "contract signature automation" OR "onboarding takes hours") lang:en -crypto -nigeria -filter:retweets',
    # Bucket 5: E-Commerce & DTC Fulfillment (Shopify / Stripe)
    '("shopify webhook" OR "abandoned cart automation" OR "inventory sync airtable" OR "order tracking whatsapp" OR "automate fulfillment") lang:en -giveaway -nigeria -filter:retweets',
    # Bucket 6: AI Customer Support & Ticket Triage
    '("automate customer support" OR "ai support bot" OR "zendesk webhook" OR "intercom automation" OR "ai triage tickets" OR "slack support bot") lang:en -crypto -nigeria -filter:retweets',
    # Bucket 7: Speed-to-Lead & High-Ticket Booking (Real Estate & Clinics)
    '("automate booking" OR "calendly webhook" OR "instant lead response" OR "speed to lead" OR "missed call text back" OR "qualify inbound") lang:en -nigeria -filter:retweets',
    # Bucket 8: Content & Media Repurposing Engines
    '("automate content repurposing" OR "transcribe podcast workflow" OR "automate newsletter" OR "repurpose video workflow") min_faves:2 lang:en -nigeria -filter:retweets',
    # Bucket 9: Overwhelmed Founder Burnout (Immediate Relief)
    '("drowning in admin work" OR "spending hours on manual tasks" OR "repetitive tasks killing my time" OR "need to automate my business") lang:en -giveaway -nigeria -filter:retweets',
    # Bucket 10: Explicit Hiring & Freelance Signals (Immediate Cash Deals)
    '("need an automation" OR "looking for n8n" OR "hire n8n" OR "hire zapier expert" OR "recommend an automation expert" OR "anyone know n8n") lang:en -nigeria -filter:retweets',
]

if not API_KEY:
    print("Error: OPENROUTER_API_KEY is not set in .env file!")
    sys.exit(1)

def setup_database():
    conn = sqlite3.connect(os.path.join(SCRIPT_DIR, DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS completed (
            tweet_id TEXT UNIQUE,
            processed_at REAL,
            author_id TEXT,
            author_handle TEXT
        )
    """)
    # Ensure columns exist if table was created previously without them
    try:
        cursor.execute("ALTER TABLE completed ADD COLUMN author_id TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE completed ADD COLUMN author_handle TEXT")
    except Exception:
        pass
    conn.commit()
    return conn

def is_already_processed(conn, tweet_id):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM completed WHERE tweet_id = ?", (str(tweet_id),))
    return cursor.fetchone() is not None

def is_author_recently_commented(conn, author_id, author_handle, hours=12):
    """Check if this author profile was already commented on within the last `hours` (default 12h)."""
    if not author_id and not author_handle:
        return False
    cutoff = time.time() - (hours * 3600)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM completed 
        WHERE (author_id = ? OR LOWER(author_handle) = LOWER(?)) 
          AND processed_at > ?
    """, (str(author_id or ''), str(author_handle or ''), cutoff))
    return cursor.fetchone() is not None

def record_processed(conn, tweet_id, author_id="", author_handle=""):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO completed (tweet_id, processed_at, author_id, author_handle) 
        VALUES (?, ?, ?, ?)
    """, (str(tweet_id), time.time(), str(author_id), str(author_handle)))
    conn.commit()

def cleanup_old_records(conn, hours=168):
    """Delete tweet IDs older than `hours` (default 7 days) so the bot never re-comments on past posts."""
    cutoff = time.time() - (hours * 3600)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM completed WHERE processed_at < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    if deleted > 0:
        print(f"[DB] Cleaned up {deleted} stale records older than {hours}h.")

def scrape_article_text(url):
    """Scrape article page briefly to get news context for short headline tweets. Retries once on failure."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                paragraphs = []
                for p in soup.find_all("p"):
                    txt = p.get_text().strip()
                    if len(txt) > 30:
                        paragraphs.append(txt)
                return " ".join(paragraphs[:3])
        except Exception as e:
            if attempt == 0:
                print(f"Link scraping attempt 1 failed ({e}). Retrying in 3s...")
                time.sleep(3)
            else:
                print(f"Link scraping skipped after 2 attempts: {e}")
    return ""

def sanitize_ai_output(content: str) -> str:
    if not content:
        return ""
    # Strip thinking tags
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    # Common preamble markers
    markers = [
        "here is my reply:", "here's my reply:", "here is the reply:", "here's the reply:",
        "my reply:", "reply:", "option 1:", "option 2:", "comment:"
    ]
    lower = content.lower()
    for m in markers:
        if m in lower:
            idx = lower.find(m)
            content = content[idx + len(m):].strip()
            break
    # Remove quotes & backticks
    content = content.replace('"', '').replace('`', '').strip()
    # Replace dashes/em-dashes/en-dashes with spaces or commas
    content = content.replace('—', ', ').replace('–', ', ')
    content = re.sub(r'\s+', ' ', content).strip()
    return content

def call_openrouter(x_post_text, article_context=""):
    context_str = f"\nAdditional Context: {article_context}" if article_context else ""

    prompt = f"""You are an 18-year-old on X (Twitter) casually talking to your friend. You reply naturally with quick banter, dry humor, playful teasing, or relatable reactions.

Rules:
- Sound like a real 18-year-old texting a friend on X: effortless, casual, unbothered, funny.
- Length: 4 to 15 words maximum. Keep it super short and punchy. Never write a paragraph or multiple sentences.
- Emojis: Use 1 or 2 natural emojis that real people use on X (such as 💀, 😭, 👀, 🤣, 😭💀). Never use corporate or cringe emojis (no 🚀🔥💡).
- For thirst traps or suggestive posts with clothes on: Never simp, never be vulgar, and never lecture or preach. Drop a funny tease or witty observation like a friend roasting them or playfully rolling their eyes.
- For 1-line posts, random memes, or hot takes: Drop an instant funny reaction, deadpan roast, or relatable comment.
- Zero AI Tells: Do NOT use formal language (like 'indeed', 'moreover', 'highlights', 'perhaps'). Write casually in simple conversational English.
- Output ONLY the reply text, no quotes, no labels, and no explanations.

Here is the post:
{x_post_text}{context_str}"""

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://automatewithjohnson.com",
        "X-Title": "AutomatesWithJohnson Auto Commenter"
    }

    fallback_models = [
        "inclusionai/ling-3.0-flash-vl",
        "meta-llama/llama-3.3-70b-instruct",
        "google/gemini-2.5-flash",
        "openai/gpt-4o-mini",
        "deepseek/deepseek-chat"
    ]
    if MODEL and MODEL not in fallback_models:
        fallback_models.insert(0, MODEL)

    for current_model in fallback_models:
        payload = {
            "model": current_model,
            "messages": [{"role": "user", "content": prompt}]
        }
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if "choices" in data and len(data["choices"]) > 0:
                    msg = data["choices"][0].get("message", {})
                    content = (msg.get("content") or "").strip()
                    if content:
                        cleaned = sanitize_ai_output(content)
                        if cleaned:
                            print(f"[AI] Comment generated via {current_model}")
                            return cleaned
        except Exception as e:
            print(f"OpenRouter model '{current_model}' failed: {e}")

    print("All OpenRouter attempts failed to generate a valid comment.")
    return None

async def setup_twitter_client():
    """Load cookies from standard JSON export and login to X without duplicate cookie conflicts"""
    client = Client("en-US", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    cookies_file = os.path.join(SCRIPT_DIR, COOKIES_PATH)
    if not os.path.exists(cookies_file) or os.path.getsize(cookies_file) < 10:
        raise Exception(f"Please paste your exported cookies into {cookies_file} first!")
        
    with open(cookies_file, "r", encoding="utf-8") as f:
        cookies_data = json.load(f)
        
    formatted_cookies = {}
    if isinstance(cookies_data, list):
        for cookie in cookies_data:
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value and name not in formatted_cookies:
                formatted_cookies[name] = str(value)
    elif isinstance(cookies_data, dict):
        formatted_cookies = {k: str(v) for k, v in cookies_data.items()}

    client.http.cookies.clear()
    for name, val in formatted_cookies.items():
        client.http.cookies.set(name, val, domain=".x.com")

    # Bypass X key_byte transaction index check for cloud execution
    if hasattr(client, 'client_transaction'):
        client.client_transaction.generate_transaction_id = lambda *args, **kwargs: "1234567890"
        
    return client

def deduplicate_cookies(client):
    """Cleanly purge duplicate cookie entries from HTTP client headers"""
    try:
        clean_dict = {}
        for name, value in client.http.cookies.items():
            if name not in clean_dict:
                clean_dict[name] = str(value)
        client.http.cookies.clear()
        for name, val in clean_dict.items():
            client.http.cookies.set(name, val, domain=".x.com")
    except Exception:
        pass

async def process_tweet_list(client, db_conn, tweets, max_posts, test_mode, my_id, source_label):
    """Shared processing loop — works for any tweet list source (Following or Search)."""
    successful_replies = 0
    for tweet in tweets:
        if successful_replies >= max_posts:
            break

        target_tweet = tweet
        reposted_by = None

        if hasattr(tweet, "retweeted_status") and tweet.retweeted_status:
            reposted_by = getattr(tweet.user, "screen_name", "unknown")
            target_tweet = tweet.retweeted_status
            print(f"\n[REPOST DETECTED] Reposted by @{reposted_by}. Targeting original: {target_tweet.id} by @{getattr(target_tweet.user, 'screen_name', 'unknown')}")

        target_author_id = getattr(target_tweet.user, 'id', '')
        target_author_handle = getattr(target_tweet.user, 'screen_name', '') or ''
        # Extract full, expanded text including "Show more" note tweets
        note_text = ""
        if hasattr(target_tweet, '_note_tweet_results') and target_tweet._note_tweet_results:
            try:
                note_text = target_tweet._note_tweet_results.get('result', {}).get('text', '')
            except Exception:
                pass
        tweet_text = note_text or getattr(target_tweet, 'full_text', None) or getattr(target_tweet, 'text', '') or ''

        is_own_tweet = (
            (my_id and str(target_author_id) == str(my_id)) or
            target_author_handle.lower() in ["alayecodes", "groundtruthdesk"] or
            "image source:" in tweet_text.lower()  # News poster signature — always our post
        )
        if is_own_tweet:
            print(f"Skipping tweet {target_tweet.id} (own tweet)")
            continue

        if is_already_processed(db_conn, target_tweet.id):
            print(f"Skipping tweet {target_tweet.id} (tweet already processed)")
            continue

        # Skip tweets with virtually no text to avoid hallucinating without vision
        clean_text_no_urls = re.sub(r'https?://[^\s]+', '', tweet_text).strip()
        if len(clean_text_no_urls) < 3:
            print(f"Skipping tweet {target_tweet.id} (no meaningful text/caption to react to)")
            continue

        tweet_time = getattr(target_tweet, "created_at_datetime", None)
        if tweet_time:
            now_utc = datetime.now(timezone.utc)
            age = now_utc - tweet_time
            if age > timedelta(hours=1):
                age_mins = int(age.total_seconds() // 60)
                print(f"Skipping tweet {target_tweet.id} (posted {age_mins}m ago > 60m limit)")
                continue

        is_quote = hasattr(target_tweet, "quoted_status") and target_tweet.quoted_status
        is_self_quote = False
        quoted_tweet = None
        quoted_text = ""

        if is_quote:
            quoted_tweet = target_tweet.quoted_status
            quoted_author_id = getattr(quoted_tweet.user, 'id', '')
            q_note = ""
            if hasattr(quoted_tweet, '_note_tweet_results') and quoted_tweet._note_tweet_results:
                try:
                    q_note = quoted_tweet._note_tweet_results.get('result', {}).get('text', '')
                except Exception:
                    pass
            quoted_text = q_note or getattr(quoted_tweet, 'full_text', None) or getattr(quoted_tweet, 'text', '') or ''

            if str(target_author_id) == str(quoted_author_id):
                is_self_quote = True
                pattern_name = "Pattern 4: Self-Quote / Thread Story Update"
            else:
                pattern_name = "Pattern 1/5: Quote Tweet"
        elif reposted_by:
            pattern_name = f"Pattern 2: Repost (Reposted by @{reposted_by})"
        else:
            pattern_name = "Pattern 3: Standard Post / News / Media"

        if reposted_by and is_quote:
            pattern_name = f"Pattern 5: Repost of Quote Tweet (Reposted by @{reposted_by})"

        print(f"\n==================================================")
        print(f"[{source_label}] {pattern_name}")
        print(f"Tweet ID: {target_tweet.id} | Author: @{target_author_handle}")
        print(f"Snippet: {tweet_text[:120]}...")
        print(f"==================================================")

        context_parts = []
        if reposted_by:
            context_parts.append(f"[Note: Reposted by @{reposted_by}]")

        if is_self_quote and quoted_tweet:
            context_parts.append(f"[Story Update by @{target_author_handle}]")
            context_parts.append(f"Previous Post: {quoted_text}")
            context_parts.append(f"Latest Update: {tweet_text}")
        elif is_quote and quoted_tweet:
            quoted_handle = getattr(quoted_tweet.user, 'screen_name', 'unknown')
            context_parts.append(f"[Quote Tweet Context]")
            context_parts.append(f"Outer (@{target_author_handle}): {tweet_text}")
            context_parts.append(f"Quoted (@{quoted_handle}): {quoted_text}")
        else:
            context_parts.append(f"Post Text: {tweet_text}")

        article_context = ""
        urls = re.findall(r'https?://[^\s]+', tweet_text)
        if urls and "t.co" in urls[0]:
            print(f"Scraping link context from {urls[0]}...")
            article_context = scrape_article_text(urls[0])

        full_payload = "\n".join(context_parts)

        try:
            print(f"Liking tweet {target_tweet.id} by @{target_author_handle}...")
            await target_tweet.favorite()
            print("Liked successfully!")
        except Exception as e:
            print(f"Like skipped/failed: {e}")

        await asyncio.sleep(1 if (test_mode or max_posts == 1) else random.randint(5, 15))

        raw_comment = call_openrouter(full_payload, article_context=article_context)
        if not raw_comment:
            print("Failed to generate comment. Skipping.")
            continue

        comment_content = sanitize_ai_output(raw_comment)
        if len(comment_content) > 245:
            comment_content = comment_content[:242].strip() + "..."

        print(f"\n--------------------------------------------------")
        print(f"COMMENT TO POST: \"{comment_content}\"")
        print(f"Length: {len(comment_content)} chars")
        print(f"--------------------------------------------------\n")

        for attempt in range(2):  # Try up to 2 times (immediate + 1 retry on 226)
            try:
                print(f"Posting reply to {target_tweet.id} (@{target_author_handle})... [attempt {attempt+1}]")
                await client.create_tweet(text=comment_content, reply_to=target_tweet.id)
                print(">>> COMMENT POSTED SUCCESSFULLY ON X! <<<")
                record_processed(db_conn, target_tweet.id, author_id=target_author_id, author_handle=target_author_handle)
                successful_replies += 1
                break
            except Exception as e:
                err_str = str(e)
                if "226" in err_str:
                    if attempt == 0:
                        print(f"[226] Temporary automation block. Waiting 60s then retrying once...")
                        await asyncio.sleep(60)
                    else:
                        print(f"[226] Retry also blocked. Skipping without recording — will retry next batch.")
                        # Do NOT record — let next batch retry this tweet
                else:
                    print(f"Failed to post comment: {e}")
                    record_processed(db_conn, target_tweet.id, author_id=target_author_id, author_handle=target_author_handle)
                    break

        if successful_replies < max_posts:
            delay = 1 if (test_mode or max_posts == 1) else random.randint(90, 150)
            print(f"Sleeping {delay}s before next post...")
            await asyncio.sleep(delay)

    return successful_replies


async def run_commenter_batch(test_mode=False, now_mode=False, max_posts=None):
    db_conn = setup_database()

    try:
        client = await setup_twitter_client()
        my_id = "1769380125886533632"
        my_username = ""
        try:
            twid_val = client.http.cookies.get("twid", "") or ""
            if "u%3D" in twid_val:
                my_id = twid_val.split("u%3D")[-1]
            elif "u=" in twid_val:
                my_id = twid_val.split("u=")[-1]
        except Exception:
            pass
        try:
            settings_resp, _ = await client.v11.settings()
            my_username = settings_resp.get("screen_name", "")
        except Exception:
            pass
        print(f"Twitter client initialized for @{my_username or 'authenticated_user'} (ID: {my_id})")
    except Exception as e:
        print(f"Failed to initialize Twitter client: {e}")
        db_conn.close()
        return

    per_source = max_posts if max_posts else (1 if test_mode else 2)
    deduplicate_cookies(client)
    cleanup_old_records(db_conn)  # Purge tweet IDs older than 48h before each batch

    # ── FYP Timeline (Home -> For You: https://x.com/home) ───────────────────
    print("\n[FYP] Fetching from For You Timeline (https://x.com/home)...")
    fyp_tweets = []
    try:
        result = await client.get_timeline(count=50)
        if result:
            fyp_tweets = list(result)
            print(f"Fetched {len(fyp_tweets)} tweets from FYP.")
    except Exception as e:
        print(f"get_timeline failed: {e}")

    posted_count = 0
    if fyp_tweets:
        posted_count = await process_tweet_list(
            client, db_conn, fyp_tweets, per_source, test_mode, my_id, "FYP"
        )
        print(f"\n[FYP DONE] Posted {posted_count}/{per_source} comments on FYP.")
    else:
        print("[FYP] No tweets found on FYP Timeline.")

    print(f"\n{'='*50}")
    print(f"BATCH COMPLETE: {posted_count} total comments posted this run on FYP.")
    print(f"{'='*50}")
    db_conn.close()

async def main():
    test_mode = "--test" in sys.argv
    now_mode = "--now" in sys.argv
    single_mode = "--single" in sys.argv or "--test" in sys.argv
    
    print("==================================================")
    print("      X FOR YOU (FYP) AUTO COMMENT BOT           ")
    if single_mode:
        print("   MODE: SINGLE TEST (1 reply only, 0 delays)    ")
    elif now_mode:
        print("   MODE: MANUAL BATCH (5 replies, 0 delays)       ")
    else:
        print(f"   Interval: Every {LOOP_INTERVAL_MINUTES} minutes")
    print("==================================================")
    
    if single_mode:
        try:
            print("\nRunning single post test on FYP...")
            await run_commenter_batch(test_mode=True, max_posts=1)
            print("\nSingle post test completed successfully!")
        except Exception as e:
            print(f"Error during test execution: {e}")
    elif now_mode:
        try:
            print("\nRunning manual auto like & comment batch on FYP...")
            await run_commenter_batch(now_mode=True)
            print("\nManual batch run completed successfully!")
        except Exception as e:
            print(f"Error during manual execution: {e}")
    else:
        while True:
            try:
                print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running FYP commenter batch...")
                await run_commenter_batch()
            except Exception as e:
                print(f"Error during execution batch: {e}")
                
            print(f"\nSleeping for {LOOP_INTERVAL_MINUTES} minutes...")
            await asyncio.sleep(LOOP_INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAuto-commenter stopped. Goodbye!")
