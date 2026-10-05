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
MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")
COOKIES_PATH = os.getenv("COOKIES_PATH", "Xaccountdata.json")
DB_PATH = "liked_comments.db"

# Target configuration
LOOP_INTERVAL_MINUTES = 30

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
            processed_at REAL
        )
    """)
    conn.commit()
    return conn

def is_already_processed(conn, tweet_id):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM completed WHERE tweet_id = ?", (str(tweet_id),))
    return cursor.fetchone() is not None

def record_processed(conn, tweet_id):
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO completed (tweet_id, processed_at) VALUES (?, ?)", (str(tweet_id), time.time()))
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
    # Replace dashes/em-dashes/en-dashes with simple spaces/commas
    content = content.replace('—', ', ').replace('–', ', ').replace('-', ' ')
    # Strip disallowed punctuation: ! ? : ;
    content = content.replace('!', '.').replace('?', '.').replace(':', ',').replace(';', ',')
    content = re.sub(r'\.+', '.', content)
    content = re.sub(r'\s+', ' ', content).strip()
    return content

def call_openrouter(x_post_text, article_context="", image_url=None):
    ai_limit = 200  # Enforce strict limit under 200 characters
    
    context_str = f"\nAdditional Context: {article_context}" if article_context else ""

    prompt = f"""You are a perceptive, insightful, and adaptable commentator on X. You participate in discussions under major accounts, breaking news aggregators, journalists, and public figures.

Task:
Analyze the X post payload below and write a short, sharp, highly engaging comment directly reacting to the post.

Rules:
- Generalist Adaptability: Seamlessly adapt your tone and thought to whatever the post is about. If it is breaking world news, conflict, or crime, give a grounded, sensible observation. If it is politics, business, tech, or daily human events, offer a smart, relatable take.
- Authenticity: Sound like an observant, thoughtful human who understands how the world works. Never sound generic or like a canned bot.
- Zero Self-Promotion: Do not promote services, tools, or links. Never say 'DM me', 'check my bio', or 'I can help'. Focus 100% on the story in front of you.
- Length Constraint: The entire reply MUST be strictly under {ai_limit} characters. Keep it brief.
- Simple English: Write in clear, plain English that anyone can read in three seconds.
- Punctuation Constraint: Do not use em dashes or en dashes anywhere. ONLY use standard commas (,) and periods (.). Do not use exclamation marks (!), question marks (?), colons (:), semicolons (;), or dashes (-) anywhere in your text. Do not ask any questions at the end of your reply.
- Output Format: Output ONLY the exact reply text. No intros, no quotation marks, no thinking tags, and no labels.

Here is the X post payload:
{x_post_text}{context_str}"""

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://automatewithjohnson.com",
        "X-Title": "AutomatesWithJohnson Auto Commenter"
    }

    # ── VISION PATH: tweet has a photo ──────────────────────────────────────
    if image_url:
        print(f"[VISION] Tweet has image. Sending to vision model with photo context...")
        vision_models = [
            "meta-llama/llama-3.2-11b-vision-instruct:free",  # Free, vision-capable
            "qwen/qwen2-vl-7b-instruct:free",                 # Free, vision-capable
            "z-ai/glm-5.3-flash",                             # Paid but cheap, real-time vision
            "google/gemini-2.5-flash",                        # Paid fallback
            "openai/gpt-4o-mini",                             # Paid fallback
        ]
        vision_message = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": prompt}
            ]
        }
        for vision_model in vision_models:
            try:
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={"model": vision_model, "messages": [vision_message]},
                    timeout=20
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if "choices" in data and len(data["choices"]) > 0:
                        content = (data["choices"][0].get("message", {}).get("content") or "").strip()
                        if content:
                            cleaned = sanitize_ai_output(content)
                            if cleaned:
                                print(f"[VISION] Comment generated via {vision_model}")
                                return cleaned
                else:
                    print(f"[VISION] {vision_model} returned {resp.status_code}. Trying next...")
            except Exception as e:
                print(f"[VISION] {vision_model} failed: {e}")
        print("[VISION] All vision models failed. Falling back to text-only...")

    # ── TEXT-ONLY PATH (also fallback when vision fails) ─────────────────────
    fallback_models = [
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
            str(target_author_id) == str(my_id) or
            target_author_handle.lower() == "alayecodes" or
            "image source:" in tweet_text.lower()  # News poster signature — always our post
        )
        if is_own_tweet or is_already_processed(db_conn, target_tweet.id):
            print(f"Skipping tweet {target_tweet.id} (own tweet or already processed)")
            continue

        tweet_time = getattr(target_tweet, "created_at_datetime", None)
        if tweet_time:
            now_utc = datetime.now(timezone.utc)
            age = now_utc - tweet_time
            if age > timedelta(hours=1):
                age_mins = int(age.total_seconds() // 60)
                print(f"Skipping tweet {target_tweet.id} (posted {age_mins}m ago > 1h limit)")
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

        # Detect photo media — pass URL to vision model. Ignore video/gif (text is enough).
        tweet_image_url = None
        tweet_media = getattr(target_tweet, "media", None)
        if tweet_media:
            for m in tweet_media:
                media_type = getattr(m, "type", "")
                if media_type == "photo":
                    # source_url gives full-resolution image; fall back to media_url
                    tweet_image_url = getattr(m, "source_url", None) or getattr(m, "media_url", None)
                    if tweet_image_url:
                        print(f"[MEDIA] Photo detected on tweet. Will send image to vision model.")
                        break
            if not tweet_image_url:
                media_type = getattr(tweet_media[0], "type", "unknown")
                print(f"[MEDIA] {media_type} detected. Using text only.")

        try:
            print(f"Liking tweet {target_tweet.id} by @{target_author_handle}...")
            await target_tweet.favorite()
            print("Liked successfully!")
        except Exception as e:
            print(f"Like skipped/failed: {e}")

        await asyncio.sleep(1 if (test_mode or max_posts == 1) else random.randint(5, 15))

        raw_comment = call_openrouter(full_payload, article_context=article_context, image_url=tweet_image_url)
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
                record_processed(db_conn, target_tweet.id)
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
                    record_processed(db_conn, target_tweet.id)  # Non-226 errors are final
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
        my_id = "2025200557"
        my_username = "AlayeCodes"
        print(f"Twitter client initialized for @{my_username} (ID: {my_id})")
    except Exception as e:
        print(f"Failed to initialize Twitter client: {e}")
        db_conn.close()
        return

    per_source = max_posts if max_posts else (1 if test_mode else 5)
    deduplicate_cookies(client)
    cleanup_old_records(db_conn)  # Purge tweet IDs older than 48h before each batch

    # ── SOURCE 1: Following Timeline ────────────────────────────────────────
    print("\n[SOURCE 1] Fetching from Following Timeline...")
    following_tweets = []
    try:
        result = await client.get_latest_timeline(count=50)
        if result:
            following_tweets = list(result)
            print(f"Fetched {len(following_tweets)} tweets from Following Timeline.")
    except Exception as e:
        print(f"get_latest_timeline failed: {e}. Trying fallback...")
        try:
            result = await client.get_timeline(count=50)
            if result:
                following_tweets = list(result)
        except Exception as e2:
            print(f"Timeline fallback also failed: {e2}")

    following_count = 0
    if following_tweets:
        following_count = await process_tweet_list(
            client, db_conn, following_tweets, per_source, test_mode, my_id, "FOLLOWING"
        )
        print(f"\n[SOURCE 1 DONE] Posted {following_count}/{per_source} comments from Following Timeline.")
    else:
        print("[SOURCE 1] No tweets found on Following Timeline.")

    # Skip buyer search in single test mode
    if test_mode or max_posts == 1:
        db_conn.close()
        return

    # ── SOURCE 2: High-Intent Buyer Buckets Rotation ────────────────────────
    selected_buckets = random.sample(BUYER_ROTATION_BUCKETS, min(2, len(BUYER_ROTATION_BUCKETS)))
    search_count = 0
    for idx, bucket_query in enumerate(selected_buckets, 1):
        print(f"\n[SOURCE 2 - BUCKET {idx}] Querying buyer intent: {bucket_query[:65]}...")
        bucket_tweets = []
        try:
            # Query Top tweets first for high-authority discussions, fallback to Latest
            result = await client.search_tweet(bucket_query, product="Top", count=25)
            if result:
                bucket_tweets = list(result)
            if not bucket_tweets:
                result = await client.search_tweet(bucket_query, product="Latest", count=25)
                if result:
                    bucket_tweets = list(result)
            print(f"Fetched {len(bucket_tweets)} candidate tweets in bucket {idx}.")
        except Exception as e:
            print(f"Buyer search for bucket {idx} failed: {e}")

        if bucket_tweets:
            bucket_quota = max(1, per_source // len(selected_buckets))
            count = await process_tweet_list(
                client, db_conn, bucket_tweets, bucket_quota, test_mode, my_id, f"BUYER BUCKET {idx}"
            )
            search_count += count
            print(f"[BUCKET {idx} DONE] Posted {count}/{bucket_quota} comments.")
        else:
            print(f"[BUCKET {idx}] No tweets found for query.")

    print(f"\n{'='*50}")
    print(f"BATCH COMPLETE: {following_count + search_count} total comments posted this run.")
    print(f"  Following Timeline: {following_count}  |  Buyer Intent Searches: {search_count}")
    print(f"{'='*50}")
    db_conn.close()

async def main():
    test_mode = "--test" in sys.argv
    now_mode = "--now" in sys.argv
    single_mode = "--single" in sys.argv or "--test" in sys.argv
    
    print("==================================================")
    print("  X FOLLOWING TIMELINE AUTO LIKE & COMMENT BOT   ")
    if single_mode:
        print("   MODE: SINGLE TEST (1 reply only, 0 delays)    ")
    elif now_mode:
        print("   MODE: MANUAL BATCH (5 replies, 0 delays)       ")
    else:
        print(f"   Interval: Every {LOOP_INTERVAL_MINUTES} minutes")
    print("==================================================")
    
    if single_mode:
        try:
            print("\nRunning single post test from Following timeline...")
            await run_commenter_batch(test_mode=True, max_posts=1)
            print("\nSingle post test completed successfully!")
        except Exception as e:
            print(f"Error during test execution: {e}")
    elif now_mode:
        try:
            print("\nRunning manual auto like & comment batch...")
            await run_commenter_batch(now_mode=True)
            print("\nManual batch run completed successfully!")
        except Exception as e:
            print(f"Error during manual execution: {e}")
    else:
        while True:
            try:
                print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running Following timeline commenter batch...")
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
