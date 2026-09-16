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

_original_user_init = twikit.user.User.__init__

def safe_user_init(self, client, data: dict) -> None:
    if isinstance(data, dict):
        legacy = data.get('legacy')
        if not isinstance(legacy, dict):
            legacy = {}
            data['legacy'] = legacy
        
        entities = legacy.get('entities')
        if not isinstance(entities, dict):
            entities = {}
            legacy['entities'] = entities
            
        url_obj = entities.get('url')
        if not isinstance(url_obj, dict):
            entities['url'] = {}
            
        desc_obj = entities.get('description')
        if not isinstance(desc_obj, dict):
            entities['description'] = {}

    try:
        _original_user_init(self, client, data)
    except Exception:
        self._client = client
        self.id = str(data.get('rest_id', '')) if isinstance(data, dict) else ''
        self.name = data.get('core', {}).get('name', '') if isinstance(data, dict) else ''
        self.screen_name = data.get('core', {}).get('screen_name', '') if isinstance(data, dict) else ''
        self.profile_image_url = ""
        self.profile_banner_url = ""
        self.url = None
        self.location = ""
        self.description = ""
        self.description_urls = []
        self.urls = []
        self.pinned_tweet_ids = []
        self.is_blue_verified = False
        self.verified = False
        self.possibly_sensitive = False
        self.can_dm = False
        self.can_media_tag = False
        self.want_retweets = False
        self.default_profile = True
        self.default_profile_image = True
        self.has_custom_timelines = False
        self.followers_count = 0
        self.fast_followers_count = 0
        self.normal_followers_count = 0
        self.following_count = 0
        self.favourites_count = 0
        self.listed_count = 0
        self.media_count = 0
        self.statuses_count = 0
        self.is_translator = False
        self.translator_type = ''
        self.withheld_in_countries = []
        self.protected = False

twikit.user.User.__init__ = safe_user_init

async def dummy_init(self, *args, **kwargs):
    self.key = "1234567890"
    self.key_bytes = [0] * 16

twikit.x_client_transaction.ClientTransaction.init = dummy_init
twikit.x_client_transaction.ClientTransaction.generate_transaction_id = lambda *args, **kwargs: "1234567890"

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
MODEL = os.getenv("OPENROUTER_MODEL", "nex-agi/nex-n2.5-mini:free")
COOKIES_PATH = os.getenv("COOKIES_PATH", "Xaccountdata.json")
DB_PATH = "liked_comments.db"

# Target configuration
LOOP_INTERVAL_MINUTES = 30
MIN_REPLIES = 0

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

def scrape_article_text(url):
    """Scrape article page briefly to get news context for short headline tweets"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
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
        print(f"Link scraping skipped: {e}")
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
    # Replace dashes/em-dashes/en-dashes with simple spaces/commas as per user rule
    content = content.replace('—', ', ').replace('–', ', ').replace('-', ' ')
    # Clean multiple spaces
    content = re.sub(r'\s+', ' ', content).strip()
    return content

def call_openrouter(x_post_text, article_context="", image_url=None):
    ai_limit = 200  # Enforce strict limit under 200 characters
    
    context_str = f"\nAdditional Context: {article_context}" if article_context else ""

    prompt = f"""You are a smart, insightful, and adaptable commentator on X. Your style is conversational, knowledgeable, friendly, and street-smart. You seamlessly adapt your commentary to WHATEVER content, topic, or niche you encounter (tech, AI, business, finance, news, sports, culture, design, or daily observations).

Task:
Analyze the X post payload below (which may include outer commentary, quoted tweets, story updates, or repost context) and write a short, sharp, highly relevant reply directly under the post.

Rules:
- Universal Adaptation & Strict Relevance: Your reply MUST directly adapt to and address the specific content, facts, or story of the post. If it is news, crime, tech, or culture, give a smart, engaging reaction.
- Natural Integration: Never force pre-written pitches. Do not advertise unless 100% appropriate.
- Length Constraint: The entire reply MUST be under {ai_limit} characters. Keep it brief.
- Simple English Constraint: Write in clear, simple English that even a kid can understand.
- Punctuation Constraint: Do not use em dashes (—) or en dashes (–) anywhere. ONLY use standard commas (,) and periods/full stops (.) for punctuation. Do not use exclamation marks (!), question marks (?), colons (:), semicolons (;), or dashes anywhere in your text. Do not ask any questions at the end of your reply.
- Add real value: Speak like someone who understands real-world facts and human nature.
- Style Variation: Vary your opening style. Sometimes start with a strong opinion, sometimes with a surprising observation.
- Sound natural and conversational.

Output Format:
CRITICAL: Output ONLY the exact reply text. Do not include any headers, labels, intros, or explanations. You MUST NOT start with "Here is my reply:" or output any thoughts. Output ONLY the comment itself.

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
        vision_models = ["z-ai/glm-5.3-flash:batch", "google/gemini-2.5-flash", "openai/gpt-4o-mini"]
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
        "nex-agi/nex-n2.5-mini:free",
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
    min_replies_needed = 0 if (test_mode or max_posts == 1) else MIN_REPLIES

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
        target_author_handle = getattr(target_tweet.user, 'screen_name', 'unknown')

        if str(target_author_id) == str(my_id) or is_already_processed(db_conn, target_tweet.id):
            print(f"Skipping tweet {target_tweet.id} (own tweet or already processed)")
            continue

        tweet_time = getattr(target_tweet, "created_at_datetime", None)
        if tweet_time:
            now_utc = datetime.now(timezone.utc)
            if (now_utc - tweet_time) > timedelta(hours=24):
                print(f"Skipping tweet {target_tweet.id} (> 24h old)")
                continue

        reply_count = getattr(target_tweet, "reply_count", 0) or 0
        if reply_count < min_replies_needed:
            print(f"Skipping tweet {target_tweet.id} (replies: {reply_count} < {min_replies_needed})")
            continue

        is_quote = hasattr(target_tweet, "quoted_status") and target_tweet.quoted_status
        is_self_quote = False
        quoted_tweet = None

        if is_quote:
            quoted_tweet = target_tweet.quoted_status
            quoted_author_id = getattr(quoted_tweet.user, 'id', '')
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
        print(f"Snippet: {target_tweet.text[:120]}...")
        print(f"==================================================")

        context_parts = []
        if reposted_by:
            context_parts.append(f"[Note: Reposted by @{reposted_by}]")

        if is_self_quote and quoted_tweet:
            context_parts.append(f"[Story Update by @{target_author_handle}]")
            context_parts.append(f"Previous Post: {quoted_tweet.text}")
            context_parts.append(f"Latest Update: {target_tweet.text}")
        elif is_quote and quoted_tweet:
            quoted_handle = getattr(quoted_tweet.user, 'screen_name', 'unknown')
            context_parts.append(f"[Quote Tweet Context]")
            context_parts.append(f"Outer (@{target_author_handle}): {target_tweet.text}")
            context_parts.append(f"Quoted (@{quoted_handle}): {quoted_tweet.text}")
        else:
            context_parts.append(f"Post Text: {target_tweet.text}")

        article_context = ""
        urls = re.findall(r'https?://[^\s]+', target_tweet.text)
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
                    tweet_image_url = getattr(m, "media_url", None)
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

        try:
            print(f"Posting reply to {target_tweet.id} (@{target_author_handle})...")
            await client.create_tweet(text=comment_content, reply_to=target_tweet.id)
            print(">>> COMMENT POSTED SUCCESSFULLY ON X! <<<")
            record_processed(db_conn, target_tweet.id)
            successful_replies += 1
        except Exception as e:
            print(f"Failed to post comment: {e}")
            record_processed(db_conn, target_tweet.id)

        if successful_replies < max_posts:
            delay = 1 if (test_mode or max_posts == 1) else random.randint(180, 300)
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

    # ── SOURCE 1: Following Timeline ────────────────────────────────────────
    print("\n[SOURCE 1] Fetching from Following Timeline...")
    following_tweets = []
    try:
        result = await client.get_latest_timeline(count=35)
        if result:
            following_tweets = list(result)
            print(f"Fetched {len(following_tweets)} tweets from Following Timeline.")
    except Exception as e:
        print(f"get_latest_timeline failed: {e}. Trying fallback...")
        try:
            result = await client.get_timeline(count=35)
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

    # Skip Nigeria search in single test mode
    if test_mode or max_posts == 1:
        db_conn.close()
        return

    # ── SOURCE 2: Nigeria Latest Search ─────────────────────────────────────
    print("\n[SOURCE 2] Fetching Latest tweets from Nigeria search...")
    search_tweets = []
    try:
        result = await client.search_tweet("nigeria", product="Latest", count=35)
        if result:
            search_tweets = list(result)
            print(f"Fetched {len(search_tweets)} tweets from Nigeria search.")
    except Exception as e:
        print(f"Nigeria search failed: {e}")

    search_count = 0
    if search_tweets:
        search_count = await process_tweet_list(
            client, db_conn, search_tweets, per_source, test_mode, my_id, "NIGERIA SEARCH"
        )
        print(f"\n[SOURCE 2 DONE] Posted {search_count}/{per_source} comments from Nigeria search.")
    else:
        print("[SOURCE 2] No tweets found in Nigeria search.")

    print(f"\n{'='*50}")
    print(f"BATCH COMPLETE: {following_count + search_count} total comments posted this run.")
    print(f"  Following Timeline: {following_count}  |  Nigeria Search: {search_count}")
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
