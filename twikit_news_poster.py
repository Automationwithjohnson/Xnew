import os
import sys
import json
import time
import sqlite3
import requests
import email.utils
import mimetypes
import tempfile
import asyncio
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
import feedparser
from twikit import Client
from dotenv import load_dotenv

# Reconfigure stdout and stderr to use UTF-8 on Windows terminal to avoid Unicode print crashes
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

# Load configuration from .env file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(SCRIPT_DIR, ".env"))

# Configuration and Constants (with environment overrides)
DB_PATH = os.getenv("DB_PATH", os.path.join(SCRIPT_DIR, "posted_links.db"))
COOKIES_PATH = os.getenv("COOKIES_PATH", os.path.join(SCRIPT_DIR, "Xaccountdata.json"))
WINDOW_MINUTES = int(os.getenv("WINDOW_MINUTES", "5"))
MEMORY_HOURS = 2

# Categories matching the old workflow
CATEGORIES = {
    "Breaking": "BREAKING",
    "General": "BREAKING",
    "Politics": "POLITICS",
    "Entertainment": "ENTERTAINMENT",
    "Sports": "SPORTS",
    "Tech": "TECH"
}

# 23 Sources remaining after cleanup
SOURCES = [
    # --- WIRE / AGGREGATORS (fastest) ---
    {"category": "Breaking", "source": "BBC Africa", "url": "http://feeds.bbci.co.uk/news/world/africa/rss.xml"},
    {"category": "General", "source": "Google News Nigeria", "url": "https://news.google.com/rss/search?q=Nigeria&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "Tech", "source": "Google News NaijaTech", "url": "https://news.google.com/rss/search?q=Nigeria+tech+startup+fintech&hl=en-NG&gl=NG&ceid=NG:en"},
    
    # --- GENERAL NEWS ---
    {"category": "General", "source": "Nairametrics", "url": "https://nairametrics.com/feed/"},
    {"category": "General", "source": "Punch Nigeria", "url": "https://punchng.com/feed/"},
    {"category": "General", "source": "Vanguard", "url": "https://www.vanguardngr.com/feed/"},
    {"category": "General", "source": "Daily Post", "url": "https://dailypost.ng/feed/"},
    {"category": "General", "source": "Legit.ng", "url": "https://news.google.com/rss/search?q=site:legit.ng&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "General", "source": "Premium Times", "url": "https://news.google.com/rss/search?q=site:premiumtimesng.com&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "General", "source": "Sahara Reporters", "url": "https://news.google.com/rss/search?q=site:saharareporters.com&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "General", "source": "PM News Nigeria", "url": "https://news.google.com/rss/search?q=site:pmnewsnigeria.com&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "General", "source": "News Online Nigeria", "url": "https://news.google.com/rss/search?q=site:newsonline.com.ng&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "General", "source": "News About Nigeria", "url": "https://news.google.com/rss/search?q=site:newsaboutnigeria.com&hl=en-NG&gl=NG&ceid=NG:en"},

    # --- POLITICS ---
    {"category": "Politics", "source": "The Nation", "url": "https://news.google.com/rss/search?q=site:thenationonlineng.net&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "Politics", "source": "Guardian Nigeria", "url": "https://news.google.com/rss/search?q=site:guardian.ng&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "Politics", "source": "The Cable", "url": "https://news.google.com/rss/search?q=site:thecable.ng&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "Politics", "source": "Nigerian Tribune", "url": "https://news.google.com/rss/search?q=site:tribuneonlineng.com&hl=en-NG&gl=NG&ceid=NG:en"},
    {"category": "Politics", "source": "Daily Report Nigeria", "url": "https://news.google.com/rss/search?q=site:dailyreport.ng&hl=en-NG&gl=NG&ceid=NG:en"},

    # --- ENTERTAINMENT / VIRAL ---
    {"category": "Entertainment", "source": "Pulse Nigeria", "url": "https://news.google.com/rss/search?q=site:pulse.ng&hl=en-NG&gl=NG&ceid=NG:en"},

    # --- SPORTS ---
    {"category": "Sports", "source": "Complete Sports", "url": "https://www.completesports.com/feed/"},
    {"category": "Sports", "source": "Goal Nigeria", "url": "https://news.google.com/rss/search?q=site:goal.com/en-ng&hl=en-NG&gl=NG&ceid=NG:en"},

    # --- TECH ---
    {"category": "Tech", "source": "Techpoint Africa", "url": "https://techpoint.africa/feed/"},
    {"category": "Tech", "source": "TechCabal", "url": "https://techcabal.com/feed/"}
]

def setup_database():
    """Setup SQLite database to track posted articles"""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posted (
            link TEXT UNIQUE,
            title TEXT,
            posted_at REAL
        )
    """)
    # Migration in case table existed without the title column
    try:
        cursor.execute("ALTER TABLE posted ADD COLUMN title TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn

def cleanup_database(conn):
    """Delete database entries older than 2 hours to keep it clean"""
    cursor = conn.cursor()
    cutoff_time = time.time() - (MEMORY_HOURS * 60 * 60)
    cursor.execute("DELETE FROM posted WHERE posted_at < ?", (cutoff_time,))
    conn.commit()

def is_similar_title(t1, t2, threshold=0.45):
    """Determine if two titles represent the same news story based on token overlap"""
    import re
    clean = lambda t: re.sub(r'[^a-z0-9\s]', '', t.lower())
    w1 = set(w for w in clean(t1).split() if len(w) > 3)
    w2 = set(w for w in clean(t2).split() if len(w) > 3)
    if not w1 or not w2:
        return False
    overlap = len(w1.intersection(w2)) / min(len(w1), len(w2))
    return overlap >= threshold

def is_duplicate_news(conn, link, title):
    """Check if the link is posted OR if a similar title was posted recently (last 24 hours)"""
    cursor = conn.cursor()
    # 1. Strict link check
    cursor.execute("SELECT 1 FROM posted WHERE link = ?", (link,))
    if cursor.fetchone() is not None:
        return True
        
    # 2. Semantic title check against last 24 hours
    cutoff_time = time.time() - (24 * 60 * 60)
    cursor.execute("SELECT title FROM posted WHERE posted_at > ? AND title IS NOT NULL", (cutoff_time,))
    rows = cursor.fetchall()
    for row in rows:
        existing_title = row[0]
        if is_similar_title(title, existing_title):
            print(f"Skipping duplicate news story (Similar to posted: '{existing_title}')")
            return True
    return False

def record_posted(conn, link, title):
    """Save link and title to database to prevent duplicates"""
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO posted (link, title, posted_at) VALUES (?, ?, ?)", (link, title, time.time()))
    conn.commit()

def clean_text(text):
    """Clean HTML and entities from text"""
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    cleaned = soup.get_text()
    return " ".join(cleaned.split()).strip()

def scrape_webpage(url):
    """Scrape article page for media URLs and paragraph text"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    media_url = None
    paragraphs = []
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None, ""
            
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 1. Look for og:video or secure_url
        og_video = soup.find("meta", property="og:video") or soup.find("meta", property="og:video:secure_url")
        if og_video and og_video.get("content"):
            media_url = og_video["content"].strip()
            
        # 2. Look for og:image if video not found
        if not media_url:
            og_image = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
            if og_image and og_image.get("content"):
                img_candidate = og_image["content"].strip()
                
                # Check for generic logos
                generic_keywords = [
                    "punch-logo", "default-logo", "placeholder", "logo-", 
                    "/logo.", "vanguardngr.com/wp-content/uploads/"
                ]
                is_generic = any(kw in img_candidate for kw in generic_keywords)
                if not is_generic:
                    media_url = img_candidate
                    
        # 3. Extract text paragraphs
        for script in soup(["script", "style"]):
            script.decompose()
            
        for p in soup.find_all("p"):
            p_text = clean_text(p.get_text())
            if len(p_text) > 40 and not p_text.startswith("ID)") and not any(
                exclude in p_text for exclude in [
                    "Read More", "Related News", "Copyright", "All rights reserved",
                    "CLICK HERE", "terms of service", "privacy policy", "subscribe to our"
                ]
            ):
                paragraphs.append(p_text)
                
    except Exception as e:
        print(f"Scraping failed for {url}: {e}")
        
    return media_url, "\n\n".join(paragraphs)

def call_openrouter(title, text, source, author, category):
    """Query OpenRouter API to draft the tweet in brand voice using deepseek model"""
    prefix = CATEGORIES.get(category, "BREAKING")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("[Warning] OPENROUTER_API_KEY is not set. Generating mock text.")
        return f"{prefix}: [Mock AI summary for {title}]"
        
    model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
    
    x_premium = os.getenv("X_PREMIUM", "true").lower() in ("true", "1", "yes")
    max_length = int(os.getenv("MAX_TWEET_LENGTH", "600"))
    
    if x_premium:
        ai_limit = max_length - 60  # Leave room for links and formatting
        limit_rule = f"Length Constraint: The entire response (including summary, quotes, and attributions) MUST be under {ai_limit} characters. Keep it brief to fit this limit."
    else:
        limit_rule = "Length Constraint: The entire response (including summary, quotes, and attributions) MUST be under 220 characters. Keep it brief to fit this limit."
    
    prompt = f"""You are a sharp, street-smart Nigerian commentator with deep knowledge of tech, finance, politics, sports, and business. Your writing style is conversational, insightful, and slightly opinionated — like a knowledgeable person explaining the news to friends on X.

Your task is to transform raw scraped news into high-quality, engaging X posts that add real value instead of just repeating headlines.

### Strict Rules:

1. {limit_rule}

2. Add genuine value — Do not just summarize. Include at least one of these:
   - A quick personal take or opinion
   - Why this news matters (especially for Nigerians or Africans)
   - Context or implication that isn't obvious in the headline
   - A smart observation or "street-smart" angle

3. Engagement — Always end the post with a natural question to encourage replies and comments.

4. Tone — Sound human, confident, and conversational. Avoid robotic or corporate language. Use light emojis only when they fit naturally (🚨, 😂, etc.).

5. "BREAKING" usage — Use "BREAKING" very sparingly. Only for truly major national or international stories. Never use it on every post.

6. Length — Keep posts concise and easy to read on mobile. Aim for 2–5 short paragraphs max.

7. Accuracy & Attribution — Keep all facts accurate. Always preserve the source credit at the bottom in this exact format:
   "Via {source}{f' | Report by {author}' if author else ''}"

8. Niche focus — When relevant, lean into Nigerian or broader African implications, challenges, or opportunities.

9. Never do this:
   - Do not copy the headline word-for-word as the main text.
   - Do not make it feel like a news aggregator bot.
   - Do not add fake information or exaggerate.
   - Do not include links or URLs in your response.

### Output Format:
Output ONLY the ready-to-post X caption. Nothing else. No explanations, no notes.

### Examples of Good Style:

Example 1 (Politics):
Input: President Bola Tinubu submits Senator Kashim Shettima as his running mate for 2027.
Output:
🚨 Tinubu just named Shettima as his running mate again for 2027.

This feels like a consolidation move rather than an attempt to broaden appeal. With the political environment tightening, I’m curious whether this strengthens their position or simply plays it safe.

What do you think — smart strategy or missed opportunity to bring in new faces?

Via Daily Post | Report by Ochogwu Sunday

Example 2 (Tech):
Input: Accrue has launched a stablecoin-powered banking platform for African SMEs.
Output:
This stablecoin platform from Accrue could actually move the needle for small businesses across Africa.

Instead of another shiny app, they’re leaning on real agent networks for faster and cheaper cross-border payments. That practical approach usually wins in markets where trust and physical presence still matter.

Do you see this kind of solution scaling better than traditional banks for SMEs in Nigeria and similar markets?

Via TechCabal | Report by Emmanuel Nwosu

Now transform the following raw news input using the rules above:

Input:
Title: {title}
Text: {text}
Category: {category}
Source: {source}
Author: {author or 'Unknown'}"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://automatewithjohnson.com",
        "X-Title": "AutomatesWithJohnson News Poster"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}]
    }
    
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        ai_text = resp.json()["choices"][0]["message"]["content"].strip()
        return ai_text
    except Exception as e:
        print(f"OpenRouter API call failed: {e}")
        return f"{title}\n\nVia {source}{f' | Report by {author}' if author else ''}"

async def setup_twitter_client():
    """Load cookies from standard JSON export and login to X"""
    client = Client("en-US")
    
    if not os.path.exists(COOKIES_PATH) or os.path.getsize(COOKIES_PATH) < 10:
        raise Exception(f"Please paste your exported cookies into {COOKIES_PATH} first!")
        
    with open(COOKIES_PATH, "r", encoding="utf-8") as f:
        cookies_data = json.load(f)
        
    if isinstance(cookies_data, list):
        formatted_cookies = {}
        for cookie in cookies_data:
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value:
                formatted_cookies[name] = value
                
        temp_cookies_path = os.path.join(SCRIPT_DIR, "temp_x_cookies.json")
        with open(temp_cookies_path, "w", encoding="utf-8") as temp_f:
            json.dump(formatted_cookies, temp_f)
            
        client.load_cookies(temp_cookies_path)
        try:
            os.remove(temp_cookies_path)
        except:
            pass
    else:
        client.load_cookies(COOKIES_PATH)
        
    return client

async def process_post(client, db_conn, article, dry_run=False):
    """Run full aggregation, AI rewrite, download and posting cycle"""
    title = article["title"]
    link = article["link"]
    category = article["category"]
    source = article["source"]
    author = article["author"]
    description = article["description"]
    
    print(f"\nProcessing new article: {title}")
    
    # 1. Scrape webpage
    media_url, page_text = scrape_webpage(link)
    if not media_url:
        print(f"Skipping article '{title}' because no media image was found.")
        record_posted(db_conn, link, title)
        return
        
    article_text = page_text if len(page_text) > 50 else description
    
    # 2. Get AI tweet text
    tweet_text = call_openrouter(title, article_text, source, author, category)
    
    # Handle character limit based on X Premium configuration
    x_premium = os.getenv("X_PREMIUM", "true").lower() in ("true", "1", "yes")
    if not x_premium and len(tweet_text) > 255:
        print(f"[Warning] Drafted text is too long ({len(tweet_text)} chars). Truncating to 255 chars to fit standard X limits.")
        tweet_text = tweet_text[:252] + "..."
        
    formatted_tweet = f"{tweet_text}\n\n{link}"
    print(f"Drafted Tweet:\n{formatted_tweet}")
    
    # 3. Download media
    temp_file_path = None
    try:
        print(f"Downloading media: {media_url}")
        resp = requests.get(media_url, stream=True, timeout=20)
        if resp.status_code == 200:
            ext = mimetypes.guess_extension(resp.headers.get("content-type", "")) or ".jpg"
            if not ext.startswith("."):
                ext = "." + ext
            if ext == ".jpe":
                ext = ".jpg"
            
            fd, temp_file_path = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            
            with open(temp_file_path, "wb") as out_file:
                for chunk in resp.iter_content(chunk_size=8192):
                    out_file.write(chunk)
            print(f"Media saved to: {temp_file_path}")
    except Exception as e:
        print(f"Media download failed: {e}")
        temp_file_path = None
        
    if not temp_file_path or not os.path.exists(temp_file_path):
        print(f"Skipping article '{title}' because the media image could not be downloaded.")
        record_posted(db_conn, link, title)
        return

    # 4. Post to Twitter
    if dry_run:
        print("Dry run mode: Skipped X posting.")
        if temp_file_path and os.path.exists(temp_file_path):
            print(f"Dry run mode: Media download path was {temp_file_path}")
        return

    try:
        media_ids = []
        if temp_file_path and os.path.exists(temp_file_path):
            print("Uploading media to Twitter via Twikit...")
            media_id = await client.upload_media(temp_file_path)
            media_ids = [media_id]
            print(f"Media uploaded. ID: {media_id}")
            
        print("Posting tweet via Twikit...")
        is_note = len(formatted_tweet) > 280
        await client.create_tweet(
            text=formatted_tweet,
            media_ids=media_ids if media_ids else None,
            is_note_tweet=is_note
        )
        print("Tweet posted successfully!")
        
        record_posted(db_conn, link, title)
        
    except Exception as e:
        print(f"Failed to post tweet: {e}")
        raise e
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                print("Cleaned up temp media file.")
            except:
                pass

async def main():
    print("=== NIGERIAN CONTENT MACHINE START ===")
    
    dry_run = "--dry-run" in sys.argv
    bypass_time_window = "--bypass-time-window" in sys.argv
    
    if dry_run:
        print("RUNNING IN DRY RUN MODE (No X posting will occur)")
    if bypass_time_window:
        print("Bypassing time window restriction (will check all recent articles)")
        
    if not dry_run and not os.path.exists(COOKIES_PATH):
        print(f"Please paste your exported cookies into {COOKIES_PATH} first!")
        return

    db_conn = setup_database()
    cleanup_database(db_conn)
    
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(minutes=WINDOW_MINUTES)
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    new_articles = []
    
    for src in SOURCES:
        try:
            print(f"Fetching: {src['source']}")
            resp = requests.get(src["url"], headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"HTTP error {resp.status_code} for {src['source']}")
                continue
                
            feed = feedparser.parse(resp.content)
            print(f"Found {len(feed.entries)} items")
            
            for entry in feed.entries:
                link = entry.get("link")
                title = entry.get("title")
                if not link or not title:
                    continue
                    
                pub_date = None
                pub_date_str = (
                    entry.get("published") or 
                    entry.get("updated") or 
                    entry.get("pubDate") or 
                    entry.get("dc:date")
                )
                
                if pub_date_str:
                    try:
                        pub_date = email.utils.parsedate_to_datetime(pub_date_str)
                        if pub_date.tzinfo is None:
                            pub_date = pub_date.replace(tzinfo=timezone.utc)
                    except:
                        pass
                        
                if not pub_date and "published_parsed" in entry and entry.published_parsed:
                    try:
                        pub_date = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                    except:
                        pass
                
                if pub_date and pub_date < cutoff and not bypass_time_window:
                    continue
                    
                cleaned_title = clean_text(title)
                if is_duplicate_news(db_conn, link.strip(), cleaned_title):
                    continue
                    
                description = entry.get("summary") or entry.get("description") or ""
                author = entry.get("author") or entry.get("creator") or entry.get("dc:creator") or ""
                
                cleaned_author = clean_text(author)
                if cleaned_author.isdigit():
                    cleaned_author = ""
                    
                new_articles.append({
                    "title": clean_text(title),
                    "link": link.strip(),
                    "category": src["category"],
                    "source": src["source"],
                    "author": cleaned_author,
                    "description": clean_text(description)
                })
        except Exception as e:
            print(f"Failed to fetch {src['source']}: {e}")
            
    print(f"\nTotal new articles found: {len(new_articles)}")
    
    if not new_articles:
        print(f"No new articles in the last {WINDOW_MINUTES} minutes.")
        db_conn.close()
        return

    # Parse limit from arguments (defaults to 5)
    limit = 5
    for arg in sys.argv:
        if arg.startswith("--limit="):
            try:
                limit = int(arg.split("=")[1])
            except:
                pass

    # Shuffle list so we don't only process the first source's articles in test/limit runs
    import random
    random.shuffle(new_articles)

    to_process = new_articles[:limit]
    print(f"Processing top {len(to_process)} articles...")
    
    twitter_client = None
    if not dry_run:
        try:
            twitter_client = await setup_twitter_client()
        except Exception as e:
            print(f"Twitter auth error: {e}")
            db_conn.close()
            return
        
    for article in to_process:
        try:
            await process_post(twitter_client, db_conn, article, dry_run=dry_run)
            if not dry_run:
                await asyncio.sleep(10)
        except Exception as e:
            print(f"Skipping article due to error: {e}")
            
    db_conn.close()
    print("\n=== NIGERIAN CONTENT MACHINE FINISHED ===")

if __name__ == "__main__":
    asyncio.run(main())
