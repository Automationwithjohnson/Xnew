import os
import re
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

# 100% Direct Native Publisher RSS Feeds (No Google News links)
SOURCES = [
    {"category": "Breaking", "source": "BBC Africa", "url": "http://feeds.bbci.co.uk/news/world/africa/rss.xml"},
    {"category": "General", "source": "Daily Post Nigeria", "url": "https://dailypost.ng/feed/"},
    {"category": "General", "source": "Nairametrics", "url": "https://nairametrics.com/feed/"},
    {"category": "General", "source": "Punch Nigeria", "url": "https://punchng.com/feed/"},
    {"category": "General", "source": "Vanguard News", "url": "https://www.vanguardngr.com/feed/"},
    {"category": "General", "source": "Premium Times", "url": "https://www.premiumtimesng.com/feed"},
    {"category": "General", "source": "Sahara Reporters", "url": "https://saharareporters.com/feed/"},
    {"category": "General", "source": "PM News Nigeria", "url": "https://pmnewsnigeria.com/feed/"},
    {"category": "Politics", "source": "The Nation", "url": "https://thenationonlineng.net/feed/"},
    {"category": "Politics", "source": "Guardian Nigeria", "url": "https://guardian.ng/feed/"},
    {"category": "Politics", "source": "The Cable", "url": "https://www.thecable.ng/feed/"},
    {"category": "Politics", "source": "Nigerian Tribune", "url": "https://tribuneonlineng.com/feed/"},
    {"category": "Entertainment", "source": "Pulse Nigeria", "url": "https://www.pulse.ng/rss"},
    {"category": "Sports", "source": "Complete Sports", "url": "https://www.completesports.com/feed/"},
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

def is_similar_title(t1, t2, threshold=0.35):
    """Determine if two titles represent the same news story across outlets based on token overlap & entities"""
    import re
    clean = lambda t: re.sub(r'[^a-z0-9\s]', '', t.lower())
    stopwords = {"says", "tells", "over", "with", "from", "after", "again", "about", "that", "this", "have", "will", "been", "first", "more", "news", "report"}
    w1 = set(w for w in clean(t1).split() if len(w) > 2 and w not in stopwords)
    w2 = set(w for w in clean(t2).split() if len(w) > 2 and w not in stopwords)
    if not w1 or not w2:
        return False
    overlap = len(w1.intersection(w2)) / min(len(w1), len(w2))
    if overlap >= threshold:
        return True
    shared = w1.intersection(w2)
    sig_words = [w for w in shared if len(w) >= 4]
    if len(sig_words) >= 2:
        return True
    return False

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

def unwrap_google_news_url(url):
    """Unwrap Google News RSS URL to get the original news publisher URL"""
    if not url or "news.google.com" not in url:
        return url
        
    try:
        parts = url.split("articles/")
        if len(parts) > 1:
            article_id = parts[1].split("?")[0]
            padded_id = article_id + "=" * (-len(article_id) % 4)
            try:
                decoded_bytes = base64.b64decode(padded_id)
                urls = re.findall(r'https?://[^\s"\'<>\x00-\x1f]+', decoded_bytes.decode('latin1', errors='ignore'))
                for u in urls:
                    if "news.google.com" not in u and "." in u:
                        clean_u = re.sub(r'[\x00-\x1f\x7f-\xff].*$', '', u)
                        return clean_u
            except Exception:
                pass
    except Exception:
        pass
        
    return url

def scrape_webpage(url):
    """Scrape article page for media URLs and paragraph text"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    media_url = None
    paragraphs = []
    
    real_url = unwrap_google_news_url(url)
    
    try:
        resp = requests.get(real_url, headers=headers, timeout=8)
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
                    "/logo.", "vanguardngr.com/wp-content/uploads/",
                    "googleusercontent.com", "google.com", "gstatic.com",
                    "play-lh.googleusercontent.com", "favicon", "apple-touch-icon",
                    "default_image", "no-image", "site-logo", "party-logo",
                    "pdp-logo", "apc-logo", "adc-logo", "coat-of-arms", "flag-of",
                    "graphic-logo", "banner-logo", "vector-logo", "badge-logo",
                    "party_logo", "apc_logo", "pdp_logo", "adc_logo"
                ]
                is_generic = any(kw in img_candidate.lower() for kw in generic_keywords)
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
        print(f"Scraping failed for {real_url}: {e}")
        
    return media_url, "\n\n".join(paragraphs)

def extract_direct_quote(text):
    """Extract a clean direct quote from scraped article text if available."""
    if not text:
        return None
        
    # Match double or single quotes "..." or '...' or “...”
    matches = re.findall(r'["“«]([^"”»]{20,220})["”»]', text)
    if matches:
        for m in matches:
            cleaned = m.strip()
            if len(cleaned) >= 20 and not any(bad in cleaned.lower() for bad in ["click here", "read more", "copyright", "subscribe", "terms of"]):
                return cleaned
                
    # If no explicit quotation marks, search for attribution verbs
    quote_verbs = [" said ", " stated ", " declared ", " warned ", " remarked ", " noted ", " added ", " explained ", " asserted "]
    sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 30]
    for s in sentences:
        if any(verb in f" {s.lower()} " for verb in quote_verbs) and len(s) <= 240:
            if not any(bad in s.lower() for bad in ["click here", "read more", "copyright", "subscribe"]):
                return s.strip()
                
    return None

def call_openrouter(title, text, source, author, category, quote=None):
    """Query OpenRouter API to draft rich, long-form news posts in brand voice with quote support."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("[Warning] OPENROUTER_API_KEY is not set.")
        return None
        
    model = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
    
    limit_rule = """Strict Length & Finishing Constraint:
- Total Response (Headline Title + Commentary Body) MUST be between 520 and 660 characters total (approx. 1 rich, deep paragraph).
- CRITICAL: Every sentence MUST be completely finished with a period. Never leave any sentence cut off or unfinished."""
    
    quote_rule = ""
    if quote:
        quote_rule = f"""\n### Direct Quote Requirement:
A direct quote or statement was found in this news story:
"{quote}"
You MUST naturally weave this quote (or a clear key excerpt of it) into your commentary body enclosed in single quotes (e.g. He said '{quote}' or As noted '{quote}'). Make sure the quote fits seamlessly within your commentary paragraph."""
    else:
        quote_rule = """\n### Direct Quote Handling:
No direct quote was found in the source article. Write a rich, deep commentary explaining the context, background, and impact naturally without fabricating any fake quotes."""

    prompt = f"""You are a sharp, street-smart Nigerian commentator with deep knowledge of tech, finance, politics, sports, and business. Your writing style is conversational, insightful, and slightly opinionated. Write like a knowledgeable insider explaining the news on X.

Your task is to transform raw news inputs (even brief 1-line headlines) into high-quality, rich, engaging X posts that add real value and context.

### Required Structure & Layout:

1. LINE 1 (Headline Title): Write a strong, clear, catchy Headline Title on the very first line. Do NOT write the word "Title:" or "Headline:". Just write the headline title directly.
2. LINE 2: Leave a blank line.
3. LINE 3+: Write the full commentary body explaining what happened, the context, economic/social impact, and your street-smart take.
{quote_rule}

### Strict Rules:

1. {limit_rule}

2. Simple English Constraint: Write in clear, simple English that is easy to read on mobile.

3. ABSOLUTELY NO EMOJIS: Do NOT use any emojis, symbols, or special icons anywhere in your text. Keep it pure text.

4. Punctuation Constraint: Do not use em dashes (—) or en dashes (–) anywhere. ONLY use standard commas (,) and periods/full stops (.) for punctuation. Do not use exclamation marks (!), question marks (?), colons (:), semicolons (;), or dashes anywhere in your text. If you ask a question at the end, end it with a period. Maintain a clean, direct sentence structure.

5. Complete Sentences Only: Every single sentence MUST be fully written and closed with a period. NEVER end mid-sentence or cut off text.

6. Engagement — Always end the commentary with a natural open question to encourage replies and comments (ended with a period instead of a question mark).

7. Tone — Sound human, confident, and conversational. Avoid robotic language.

8. Never do this:
    - Do NOT write the word "Title:" or "Headline:".
    - Do NOT use any emojis.
    - Do NOT leave any sentence unfinished or cut off.
    - Do NOT include links or URLs inside your AI text.
    - Do NOT include source credit lines (the source credit is attached automatically).

Output Format:
Output ONLY the post text (Title on line 1, blank line, then commentary body). Nothing else.

Input to Expand:
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
    
    fallback_models = [
        "meta-llama/llama-3.3-70b-instruct",
        "google/gemini-2.5-flash",
        "openai/gpt-4o-mini",
        "deepseek/deepseek-chat"
    ]
    if model and model not in fallback_models:
        fallback_models.insert(0, model)
    
    for current_model in fallback_models:
        if not current_model:
            continue
        payload = {
            "model": current_model,
            "messages": [{"role": "user", "content": prompt}]
        }
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                if "choices" in data and len(data["choices"]) > 0:
                    ai_text = data["choices"][0]["message"]["content"].strip()
                    return ai_text
        except Exception as e:
            print(f"[WARN] OpenRouter model '{current_model}' failed: {e}")

    return None

async def setup_twitter_client():
    """Load cookies from standard JSON export and login to X without duplicate cookie conflicts"""
    client = Client("en-US", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    if not os.path.exists(COOKIES_PATH) or os.path.getsize(COOKIES_PATH) < 10:
        raise Exception(f"Please paste your exported cookies into {COOKIES_PATH} first!")
        
    with open(COOKIES_PATH, "r", encoding="utf-8") as f:
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
        print(f"No media image for '{title}'. Proceeding with text news post.")
        
    article_text = page_text if len(page_text) > 50 else description
    quote = extract_direct_quote(article_text)
    if quote:
        print(f"Extracted direct quote: '{quote[:70]}...'")
        
    # 2. Get AI tweet text
    tweet_text = call_openrouter(title, article_text, source, author, category, quote=quote)
    if not tweet_text:
        print(f"Skipping article '{title}' because AI text generation failed.")
        return False
        
    source_line = f"Via {source}" + (f" | Report by {author}" if author else "")
    link_len_on_x = 23 if link.startswith("http") else len(link)
    overhead = len(source_line) + link_len_on_x + 6  # newlines
    
    max_body_len = max(450, 740 - overhead)
    min_body_len = max(350, 600 - overhead)
    
    # 1. Truncate if body is too long
    if len(tweet_text) > max_body_len:
        truncated = tweet_text[:max_body_len]
        last_dot = truncated.rfind('.')
        if last_dot > 300:
            tweet_text = truncated[:last_dot + 1].strip()
        else:
            last_space = truncated.rfind(' ')
            if last_space > 250:
                tweet_text = truncated[:last_space].strip() + "."
            else:
                tweet_text = truncated.strip() + "."

    # 2. Pad from article_text if body is too short
    if len(tweet_text) < min_body_len and article_text:
        extra_sentences = [s.strip() for s in article_text.split('.') if len(s.strip()) > 15]
        for sentence in extra_sentences:
            if sentence in tweet_text:
                continue
            candidate = f"{tweet_text} {sentence}."
            if len(candidate) <= max_body_len:
                tweet_text = candidate
            if len(tweet_text) >= min_body_len:
                break

    formatted_tweet = f"{tweet_text}\n\n{source_line}\n\n{link}"
    
    # Hard safety check: re-trim if total post exceeds 745 characters
    if len(formatted_tweet) > 745:
        max_safe_body = 745 - overhead
        truncated = tweet_text[:max_safe_body]
        last_dot = truncated.rfind('.')
        if last_dot > 300:
            tweet_text = truncated[:last_dot + 1].strip()
        else:
            tweet_text = truncated.strip() + "."
        formatted_tweet = f"{tweet_text}\n\n{source_line}\n\n{link}"

    # Final Guard: Skip only if formatted length is under 550 chars after padding
    if len(formatted_tweet) < 550:
        print(f"Skipping article '{title}' because total length ({len(formatted_tweet)} chars) is below minimum of 550 chars.")
        return False
        
    print(f"Drafted Tweet ({len(formatted_tweet)} chars):\n{formatted_tweet}")
    
    # 3. Download media
    temp_file_path = None
    if media_url:
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
        
    # Enforce mandatory media image attachment for every news post
    if not temp_file_path or not os.path.exists(temp_file_path):
        print(f"Skipping article '{title}' because media image could not be downloaded or was not found.")
        return False

    # 4. Post to Twitter
    if dry_run:
        print("Dry run mode: Skipped X posting.")
        if temp_file_path and os.path.exists(temp_file_path):
            print(f"Dry run mode: Media download path was {temp_file_path}")
            try:
                os.remove(temp_file_path)
            except:
                pass
        return True

    try:
        media_ids = []
        if temp_file_path and os.path.exists(temp_file_path):
            print("Uploading media to Twitter via Twikit...")
            media_id = await client.upload_media(temp_file_path)
            media_ids = [media_id]
            print(f"Media uploaded. ID: {media_id}")
            
        is_note = len(formatted_tweet) > 280
        await client.create_tweet(
            text=formatted_tweet,
            media_ids=media_ids if media_ids else None,
            is_note_tweet=is_note
        )
        print("Tweet posted successfully!")
        
        record_posted(db_conn, link, title)
        return True
        
    except Exception as e:
        print(f"Failed to post tweet: {e}")
        return False
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
                link = unwrap_google_news_url(entry.get("link"))
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
    
    if not new_articles and not bypass_time_window:
        print(f"No new articles in the last {WINDOW_MINUTES} minutes. Running fallback pass across all recent articles...")
        for src in SOURCES:
            try:
                resp = requests.get(src["url"], headers=headers, timeout=15)
                if resp.status_code != 200:
                    continue
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    link = entry.get("link")
                    title = entry.get("title")
                    if not link or not title:
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
            except Exception:
                pass

    if not new_articles:
        print(f"No unposted articles available across all sources.")
        db_conn.close()
        return

    # Parse limit from arguments (defaults to 4)
    limit = 4
    for arg in sys.argv:
        if arg.startswith("--limit="):
            try:
                limit = int(arg.split("=")[1])
            except:
                pass

    # Shuffle list so we don't only process the first source's articles in test/limit runs
    import random
    random.shuffle(new_articles)

    print(f"Processing new articles dynamically to reach target limit of {limit} successful posts...")
    
    twitter_client = None
    if not dry_run:
        try:
            twitter_client = await setup_twitter_client()
        except Exception as e:
            print(f"Twitter auth error: {e}")
            db_conn.close()
            return
        
    successful_posts = 0
    for article in new_articles:
        if successful_posts >= limit:
            print(f"Reached post limit: {limit}. Stopping batch.")
            break
            
        try:
            posted = await process_post(twitter_client, db_conn, article, dry_run=dry_run)
            if posted:
                successful_posts += 1
                if not dry_run and successful_posts < limit:
                    import random
                    delay = random.randint(120, 240)
                    print(f"Waiting for {delay / 60:.1f} minutes before posting next story...")
                    await asyncio.sleep(delay)
        except Exception as e:
            print(f"Skipping article due to error: {e}")
            
    db_conn.close()
    print("\n=== NIGERIAN CONTENT MACHINE FINISHED ===")

if __name__ == "__main__":
    asyncio.run(main())
