import os
import sys
import time
import sqlite3
import random
import json
import asyncio
import requests
import re
import base64
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
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
MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
COOKIES_PATH = os.getenv("COOKIES_PATH", "Xaccountdata.json")
DB_PATH = "liked_comments.db"

# Target configuration
LOOP_INTERVAL_MINUTES = 30
MIN_REPLIES = 0  # Reply to any target post matching queries
SEARCH_QUERIES = [
    "tech OR technology OR AI OR LLM filter:images",
    "business OR finance OR startup OR economy filter:images",
    "news OR breaking OR sports OR culture filter:images",
    "productivity OR software OR coding OR design filter:images"
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
    cursor.execute("SELECT 1 FROM completed WHERE tweet_id = ?", (tweet_id,))
    return cursor.fetchone() is not None

def record_processed(conn, tweet_id):
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO completed (tweet_id, processed_at) VALUES (?, ?)", (tweet_id, time.time()))
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

def get_base64_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def call_openrouter(x_post_text, article_context="", image_path=None):
    ai_limit = 200  # Enforce strict limit under 250 characters
    
    context_str = f"\nAdditional Context: {article_context}" if article_context else ""
    
    image_instruction = ""
    if image_path:
        image_instruction = "- Analyze both the text and the image. Use details from the image (the charts, the branding, the people, the text in the image) to write a customized reaction comment.\n"

    prompt = f"""You are a smart, insightful, and adaptable commentator on X. Your style is conversational, knowledgeable, friendly, and street-smart. You seamlessly adapt your commentary to WHATEVER content, topic, or niche you encounter (tech, AI, business, finance, news, sports, culture, design, or daily observations).

Task:
Analyze the X post (and any image/link context provided) and write a short, sharp, highly relevant reply directly under the post.

Rules:

- Universal Adaptation & Strict Relevance: Your reply MUST directly adapt to and address the specific content, facts, or story of the post. If the post is about tech or business, give an insider tech/business take. If the post is about news, sports, or culture, give a smart, engaging commentary on that exact event.
- Natural Integration: Never force pre-written pitches. Only mention automation, workflows, or systems if the post specifically talks about manual tasks, data entry, CRM syncs, or software tools where it fits 100% naturally. Otherwise, just write a sharp, smart reaction to the post topic itself.
- Length Constraint: The entire reply MUST be under {ai_limit} characters. Keep it brief.
- Simple English Constraint: Write in clear, simple English that even a kid can understand.
- Punctuation Constraint: Do not use em dashes (—) or en dashes (–) anywhere. ONLY use standard commas (,) and periods/full stops (.) for punctuation. Do not use exclamation marks (!), question marks (?), colons (:), semicolons (;), or dashes anywhere in your text. Do not ask any questions at the end of your reply. Make it a direct statement or opinion.
- Add real value: Speak like someone who understands real-world facts, human nature, and growth.
{image_instruction}- Style Variation: Vary your opening style. Sometimes start with a strong opinion, sometimes with a surprising observation, and sometimes with a direct statement.
- Sound natural and conversational (not robotic or corporate).
- Keep it relatively short and easy to read on mobile.
- Do not use any punctuation marks other than standard periods and commas.

Output Format (Follow this exactly):
CRITICAL: Output ONLY the exact reply text. Do not include any headers, labels, intros, or explanations. You MUST NOT start with "Here is my reply:" or output any thoughts or reasons about the tweet. Output ONLY the comment itself.

Here is the X post:
{x_post_text}{context_str}"""

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://automatewithjohnson.com",
        "X-Title": "AutomatesWithJohnson Auto Commenter"
    }
    
    model_to_use = MODEL
    
    payloads_to_try = []
    # Try multimodal payload if image exists
    if image_path:
        try:
            base64_image = get_base64_image(image_path)
            payloads_to_try.append({
                "model": "google/gemini-2.5-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }
                ]
            })
        except Exception as e:
            print(f"Base64 image encoding failed, falling back to text: {e}")
            
    # Always include text-only payload as primary or fallback
    payloads_to_try.append({
        "model": model_to_use,
        "messages": [{"role": "user", "content": prompt}]
    })

    for payload in payloads_to_try:
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=40)
            if resp.status_code == 200:
                data = resp.json()
                if "choices" in data and len(data["choices"]) > 0:
                    msg = data["choices"][0].get("message", {})
                    content = (msg.get("content") or "").strip()
                    if content:
                        # Clean up inner monologue or headers
                        lower_content = content.lower()
                        markers = [
                            "here is my reply:",
                            "here's my reply:",
                            "here is the reply:",
                            "here's the reply:",
                            "my reply:",
                            "reply:"
                        ]
                        for marker in markers:
                            if marker in lower_content:
                                idx = lower_content.find(marker)
                                content = content[idx + len(marker):].strip()
                                break
                                
                        content = content.replace('"', '').strip()
                        return content
                else:
                    print(f"OpenRouter status 200 missing choices: {data}")
            else:
                print(f"OpenRouter payload returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"OpenRouter attempt failed: {e}")

    print("All OpenRouter attempts failed to generate a valid comment.")
    return None

async def setup_twitter_client():
    """Load cookies from standard JSON export and login to X"""
    client = Client("en-US")
    
    cookies_file = os.path.join(SCRIPT_DIR, COOKIES_PATH)
    if not os.path.exists(cookies_file) or os.path.getsize(cookies_file) < 10:
        raise Exception(f"Please paste your exported cookies into {cookies_file} first!")
        
    with open(cookies_file, "r", encoding="utf-8") as f:
        cookies_data = json.load(f)
        
    if isinstance(cookies_data, list):
        formatted_cookies = {}
        for cookie in cookies_data:
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value:
                formatted_cookies[name] = value
        cookies_data = formatted_cookies
        
        temp_cookies_path = os.path.join(SCRIPT_DIR, "temp_x_cookies_comment.json")
        with open(temp_cookies_path, "w", encoding="utf-8") as temp_f:
            json.dump(formatted_cookies, temp_f)
            
        client.load_cookies(temp_cookies_path)
        try:
            os.remove(temp_cookies_path)
        except:
            pass
    else:
        client.load_cookies(cookies_file)
        
    return client

async def run_commenter_batch(test_mode=False, now_mode=False):
    db_conn = setup_database()
    
    try:
        client = await setup_twitter_client()
        my_username = os.getenv("MY_USERNAME", "AlayeCodes")
        my_id = os.getenv("MY_ID", "2025200557")
        print(f"Twitter client initialized for @{my_username} (ID: {my_id})")
    except Exception as e:
        print(f"Twitter login failed: {e}")
        db_conn.close()
        return

    min_replies_needed = 0 if test_mode else MIN_REPLIES
    successful_replies = 0
    max_replies_to_post = 3 if test_mode else 5

    tweets = []
    for query in SEARCH_QUERIES:
        # Reset the client transaction state if a previous request left it half-initialized
        if hasattr(client, 'client_transaction'):
            ct = client.client_transaction
            if ct.home_page_response and not hasattr(ct, 'key'):
                print("Detected half-initialized client transaction. Resetting transaction state...")
                ct.home_page_response = None
                
        print(f"Searching X for Top posts matching: '{query}'...")
        try:
            results = await client.search_tweet(query, 'Top', count=10)
            print(f"Found {len(results)} tweets for query '{query}'")
            tweets.extend(results)
        except Exception as e:
            print(f"Failed to fetch search results for query '{query}': {e}")
            if hasattr(client, 'client_transaction'):
                client.client_transaction.home_page_response = None

    # Shuffle the gathered tweets to randomize the mix of tech, AI, and business
    random.shuffle(tweets)
    print(f"Total tweets gathered for evaluation: {len(tweets)}")
    if not tweets:
        print("No tweets found matching any of the queries.")
        db_conn.close()
        return

    for tweet in tweets:
        if successful_replies >= max_replies_to_post:
            break

        print(f"Evaluating tweet {tweet.id} by @{tweet.user.screen_name} (Created: {tweet.created_at_datetime})")

        # 1. Skip if it is our own tweet or we already processed it
        if tweet.user.id == my_id or is_already_processed(db_conn, tweet.id):
            continue

        # 2. Skip retweets
        if hasattr(tweet, "retweeted_status") and tweet.retweeted_status:
            continue

        # 2.5 Skip if the tweet contains video or animated GIF media
        if hasattr(tweet, "media") and tweet.media:
            if any(m.type in ["video", "animated_gif"] for m in tweet.media):
                print(f"Skipping tweet {tweet.id} (contains video or GIF)")
                continue

            # 3. Check if the tweet was posted within the last 24 hours
            tweet_time = tweet.created_at_datetime
            now_utc = datetime.now(timezone.utc)
            if (now_utc - tweet_time) > timedelta(hours=24):
                print(f"Skipping tweet {tweet.id} (posted {tweet_time} which is older than 24 hours)")
                continue

            # 4. Check for minimum replies to target active discussions
            reply_count = getattr(tweet, "reply_count", 0) or 0
            if reply_count < min_replies_needed:
                print(f"Skipping tweet {tweet.id} (replies: {reply_count} < {min_replies_needed})")
                continue

            print(f"\nTarget post matches! (ID: {tweet.id}, Author: @{tweet.user.screen_name}, Replies: {reply_count})")
            print(f"Text: {tweet.text[:100]}...")

            # Extract external links and scrape text context if possible
            article_context = ""
            urls = re.findall(r'https?://[^\s]+', tweet.text)
            if urls:
                # Use first URL
                target_url = urls[0]
                # Filter out standard t.co media references
                if "t.co" in target_url:
                    print(f"Scraping link context from: {target_url}")
                    article_context = scrape_article_text(target_url)
                    if article_context:
                        print(f"Scraped context: {article_context[:100]}...")

            # 4. Like the tweet
            try:
                print("Liking the tweet...")
                await tweet.favorite()
                print("Tweet liked successfully!")
            except Exception as e:
                print(f"Failed to like tweet: {e}")

            # Wait between Like and Comment (1s in test/now mode, 5-15s in prod)
            await asyncio.sleep(1 if (test_mode or now_mode) else random.randint(5, 15))

            # 5. Check for image media and download if present
            temp_img_path = None
            if hasattr(tweet, "media") and tweet.media:
                photos = [m for m in tweet.media if m.type == "photo"]
                if photos:
                    photo_media = photos[0]
                    temp_img_path = os.path.join(SCRIPT_DIR, f"temp_tweet_img_{tweet.id}.jpg")
                    try:
                        print("Downloading post image for AI vision...")
                        await photo_media.download(temp_img_path)
                        print("Photo downloaded successfully.")
                    except Exception as e:
                        print(f"Failed to download image, falling back to text-only: {e}")
                        temp_img_path = None

            # 6. Generate reply content
            comment_content = call_openrouter(tweet.text, article_context, temp_img_path)
            
            # Clean up temp image
            if temp_img_path:
                try:
                    os.remove(temp_img_path)
                except:
                    pass

            if not comment_content:
                print("Failed to generate comment. Skipping.")
                continue

            # Programmatically enforce the under 250-character limit
            if len(comment_content) > 245:
                comment_content = comment_content[:242].strip() + "..."

            print(f"Drafted Comment:\n{comment_content} (Length: {len(comment_content)})")

            # 6. Post the reply/comment
            try:
                print("Posting comment...")
                await client.create_tweet(text=comment_content, reply_to=tweet.id)
                print("Comment posted successfully!")
                record_processed(db_conn, tweet.id)
                successful_replies += 1
            except Exception as e:
                print(f"Failed to post comment: {e}")
                # Save to database to prevent infinite retries
                record_processed(db_conn, tweet.id)

            # Wait before moving to next match (0s in test, 3-5 minutes in prod)
            if successful_replies < max_replies_to_post:
                delay = 1 if (test_mode or now_mode) else random.randint(180, 300)
                print(f"Sleeping for {delay} seconds before checking other accounts...")
                await asyncio.sleep(delay)

    db_conn.close()

async def main():
    test_mode = "--test" in sys.argv
    now_mode = "--now" in sys.argv
    
    print("==================================================")
    print("      X AUTO LIKE & COMMENT BOT STARTING...       ")
    if test_mode:
        print("   MODE: TEST MODE (3 replies only, zero delays) ")
    elif now_mode:
        print("   MODE: MANUAL RUN (5 replies only, zero delays) ")
    else:
        print(f"   Interval: Every {LOOP_INTERVAL_MINUTES} minutes")
        print(f"   Target Threshold: {MIN_REPLIES}+ replies minimum")
        print(f"   Production Delay: Random 3-5 minutes between posts")
    print("   Press Ctrl+C to terminate the loop")
    print("==================================================")
    
    if test_mode:
        try:
            print("\nRunning auto like & comment test batch...")
            await run_commenter_batch(test_mode=True)
            print("\nTest completed successfully!")
        except Exception as e:
            print(f"Error during test execution: {e}")
    elif now_mode:
        try:
            print("\nRunning manual auto like & comment batch...")
            await run_commenter_batch(test_mode=False, now_mode=True)
            print("\nManual run completed successfully!")
        except Exception as e:
            print(f"Error during manual execution: {e}")
    else:
        while True:
            try:
                print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Running auto like & comment batch...")
                await run_commenter_batch(test_mode=False)
            except Exception as e:
                print(f"Error during execution batch: {e}")
                
            print(f"\nSleeping for {LOOP_INTERVAL_MINUTES} minutes...")
            await asyncio.sleep(LOOP_INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAuto-commenter stopped. Goodbye!")


