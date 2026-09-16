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
MEMORY_HOURS = 18

# Categories matching the old workflow
CATEGORIES = {
    "Breaking": "BREAKING",
    "General": "BREAKING",
    "Politics": "POLITICS",
    "Entertainment": "ENTERTAINMENT",
    "Sports": "SPORTS",
    "Tech": "TECH"
}

# 11 Curated Premium Native Publisher RSS Feeds (Cut Pulse, PM News, Tribune, Daily Post)
SOURCES = [
    {"category": "Breaking", "source": "BBC Africa", "url": "http://feeds.bbci.co.uk/news/world/africa/rss.xml"},
    {"category": "General", "source": "Nairametrics", "url": "https://nairametrics.com/feed/"},
    {"category": "General", "source": "Punch Nigeria", "url": "https://punchng.com/feed/"},
    {"category": "General", "source": "Vanguard News", "url": "https://www.vanguardngr.com/feed/"},
    {"category": "General", "source": "Premium Times", "url": "https://www.premiumtimesng.com/feed"},
    {"category": "General", "source": "Sahara Reporters", "url": "https://saharareporters.com/feed/"},
    {"category": "Politics", "source": "The Nation", "url": "https://thenationonlineng.net/feed/"},
    {"category": "Politics", "source": "Guardian Nigeria", "url": "https://guardian.ng/feed/"},
    {"category": "Politics", "source": "The Cable", "url": "https://www.thecable.ng/feed/"},
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
    """Delete database entries older than 18 hours to keep it clean"""
    cursor = conn.cursor()
    cutoff_time = time.time() - (MEMORY_HOURS * 60 * 60)
    cursor.execute("DELETE FROM posted WHERE posted_at < ?", (cutoff_time,))
    conn.commit()

def normalize_title(t):
    """Normalize title for exact and fuzzy deduplication."""
    if not t:
        return ""
    t_clean = re.sub(r'[-|]\s*(Punch|Vanguard|TheCable|Guardian|Sahara Reporters|Premium Times|BBC|The Nation|TechCabal).*$', '', t, flags=re.IGNORECASE)
    t_clean = re.sub(r'[^a-z0-9\s]', '', t_clean.lower())
    return t_clean.strip()

def has_new_fact(new_title, old_title):
    """Check if new_title contains a NEW FACT (new numbers, scores, FX rates, or new entities) compared to old_title."""
    # Generic prefixes like 'just in', 'update', 'breaking' alone do NOT count as a new fact
    nums_new = set(re.findall(r'\b\d+(?:\.\d+)?(?:k|m|b|tn|%|bn)?\b', new_title.lower()))
    nums_old = set(re.findall(r'\b\d+(?:\.\d+)?(?:k|m|b|tn|%|bn)?\b', old_title.lower()))
    
    # If new title has numbers/amounts not present in old title -> NEW FACT!
    new_nums = nums_new - nums_old
    if new_nums:
        return True
        
    # Extract capitalized proper nouns / entities
    words_new = set(w for w in re.findall(r'\b[A-Z][a-z]+\b', new_title) if len(w) > 3)
    words_old = set(w for w in re.findall(r'\b[A-Z][a-z]+\b', old_title) if len(w) > 3)
    
    ignore_words = {"Punch", "Vanguard", "TheCable", "Guardian", "Sahara", "Premium", "Times", "Breaking", "Update", "Just", "News", "Report"}
    words_new -= ignore_words
    words_old -= ignore_words
    
    new_entities = words_new - words_old
    if len(new_entities) >= 2:
        return True
        
    return False

def is_similar_title(t1, t2, threshold=0.60):
    """Determine if two titles represent the same news story across outlets based on token overlap & entities."""
    stopwords = {"says", "tells", "over", "with", "from", "after", "again", "about", "that", "this", "have", "will", "been", "first", "more", "news", "report", "just", "update", "breaking"}
    c1 = normalize_title(t1)
    c2 = normalize_title(t2)
    w1 = set(w for w in c1.split() if len(w) > 2 and w not in stopwords)
    w2 = set(w for w in c2.split() if len(w) > 2 and w not in stopwords)
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
    """Check if link was posted OR if a similar title was posted within 18 hours without a new fact."""
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM posted WHERE link = ?", (link,))
    if cursor.fetchone() is not None:
        return True
        
    cutoff_time = time.time() - (MEMORY_HOURS * 60 * 60)
    cursor.execute("SELECT title FROM posted WHERE posted_at > ? AND title IS NOT NULL", (cutoff_time,))
    rows = cursor.fetchall()
    for row in rows:
        existing_title = row[0]
        if is_similar_title(title, existing_title):
            if has_new_fact(title, existing_title):
                print(f"[INFO] Allowing story update for '{title}' (Contains new facts vs '{existing_title}')")
                return False
            else:
                print(f"Skipping duplicate news story (Similar to posted in last 18h: '{existing_title}')")
                return True
    return False

def is_low_value(title, description, body_text=""):
    """Filter out sponsored content, ads, betting, listicles, birthdays, routine admin notices, and thin body (<400 chars)."""
    text_to_check = f"{title} {description}".lower()
    
    # 1. Skip promo/ad/filler keywords
    bad_keywords = [
        "sponsored", "advert", "advertorial", "partner content", "promoted",
        "betting", "predict and win", "betting tips", "odds", "horoscope", "lottery",
        "discount", "how to apply", "how to buy", "press release", "happy birthday",
        " rip ", "photos:", "in pictures", "see photos", "just in pictures"
    ]
    if any(kw in text_to_check for kw in bad_keywords):
        return True, "Contains ad/promo/filler keyword"
        
    # 2. Skip routine admin fluff unless vital national figure/institution is present
    admin_keywords = ["road closed", "commiserates", "inaugurates", "flags off", "charges youths", "tasks members", "appoints special assistant"]
    vital_entities = ["tinubu", "cbn", "fg", "presidency", "efcc", "super eagles", "naira", "oil", "court", "supreme court", "dollar", "inec"]
    if any(ak in text_to_check for ak in admin_keywords):
        if not any(ve in text_to_check for ve in vital_entities):
            return True, "Routine local administrative notice"
            
    # 3. Skip thin body (<400 chars)
    if body_text and len(body_text.strip()) < 400:
        return True, f"Thin body content ({len(body_text.strip())} chars < 400 min)"
        
    return False, ""

def score_article(article, body_text=""):
    """Score article relevance and impact (higher score = posted first)."""
    text = f"{article.get('title', '')} {article.get('description', '')} {body_text}".lower()
    score = 0
    
    # High Impact Topics (+3)
    high_impact = ["tinubu", "cbn", "naira", "inflation", "oil", "fuel", "court", "efcc", "inec", "presidency", "super eagles", "afcon", "transfer"]
    if any(k in text for k in high_impact):
        score += 3
        
    # Medium Impact (+2)
    med_impact = ["tech", "startup", "funding", "ncc", "police", "senate", "house of reps", "governor"]
    if any(k in text for k in med_impact):
        score += 2
        
    # Premium Source (+2)
    if article.get("source") in ["BBC Africa", "Premium Times", "The Cable", "Nairametrics"]:
        score += 2
        
    # Low Value Penalty (-2)
    if any(k in text for k in ["actor", "actress", "socialite", "bizarre", "drama"]):
        score -= 2
        
    return score

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
    """Scrape article page for video/image media URLs and paragraph text"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
    media_url = None
    media_type = "image"
    fallback_image = None
    paragraphs = []
    
    real_url = unwrap_google_news_url(url)
    
    try:
        resp = requests.get(real_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None, "image", "", None
            
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 1. Search for video meta tags or HTML video elements
        og_video = soup.find("meta", property="og:video") or soup.find("meta", property="og:video:secure_url") or soup.find("meta", attrs={"name": "twitter:player"})
        if og_video and og_video.get("content"):
            cand = og_video["content"].strip()
            if cand and not cand.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                media_url = cand
                media_type = "video"

        if not media_url:
            for iframe in soup.find_all("iframe"):
                src = iframe.get("src") or iframe.get("data-src") or ""
                if any(v in src for v in ["youtube.com/embed", "youtu.be", "dailymotion.com", "vimeo.com"]):
                    media_url = src.strip()
                    media_type = "video"
                    break

        if not media_url:
            for video in soup.find_all("video"):
                src = video.get("src")
                if src:
                    media_url = src.strip()
                    media_type = "video"
                    break
                for s in video.find_all("source"):
                    if s.get("src"):
                        media_url = s["src"].strip()
                        media_type = "video"
                        break
                        
        # 2. Extract og:image as featured or fallback image
        og_image = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
        if og_image and og_image.get("content"):
            img_candidate = og_image["content"].strip()
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
            if not any(kw in img_candidate.lower() for kw in generic_keywords):
                fallback_image = img_candidate
                if not media_url:
                    media_url = img_candidate
                    media_type = "image"
                    
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
        
    return media_url, media_type, "\n\n".join(paragraphs), fallback_image

def download_media(media_url, media_type, fallback_image_url=None):
    """Download video clip or featured image, with automatic fallback to image if video fails."""
    if not media_url:
        return None
        
    temp_dir = tempfile.gettempdir()
    
    if media_type == "video":
        print(f"Attempting video download from: {media_url}")
        out_tmpl = os.path.join(temp_dir, f"tweet_media_{int(time.time())}.mp4")
        
        # Direct MP4 download
        if media_url.endswith(".mp4") and not ("youtube.com" in media_url or "youtu.be" in media_url):
            try:
                resp = requests.get(media_url, stream=True, timeout=20)
                if resp.status_code == 200:
                    with open(out_tmpl, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    if os.path.exists(out_tmpl) and os.path.getsize(out_tmpl) > 50000:
                        print(f"Direct MP4 video saved: {out_tmpl}")
                        return out_tmpl
            except Exception as e:
                print(f"Direct MP4 video download failed: {e}")

        # Download video clip via yt-dlp
        import yt_dlp
        ydl_opts = {
            'format': 'mp4[height<=720]/best[ext=mp4]/best',
            'outtmpl': out_tmpl,
            'quiet': True,
            'no_warnings': True,
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
            'download_ranges': yt_dlp.utils.download_range_func(None, [(0, 45)]),
            'force_keyframes_at_cuts': True
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([media_url])
                if os.path.exists(out_tmpl) and os.path.getsize(out_tmpl) > 50000:
                    print(f"Video clip saved via yt-dlp: {out_tmpl}")
                    return out_tmpl
        except Exception as e:
            print(f"yt-dlp video download failed: {e}")
            
        print("Video download failed. Falling back to featured article image...")
        media_url = fallback_image_url
        media_type = "image"
        
    if media_type == "image" and media_url:
        try:
            print(f"Downloading featured image: {media_url}")
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
                print(f"Featured image saved: {temp_file_path}")
                return temp_file_path
        except Exception as e:
            print(f"Featured image download failed: {e}")
            
    return None

def extract_direct_quote(text):
    """Extract a clean direct quote from scraped article text if available."""
    if not text:
        return None
        
    # Match double or single quotes "..." or "..." or «...»
    matches = re.findall(r'["\u201c\u00ab]([^"\u201d\u00bb]{20,220})["\u201d\u00bb]', text)
    if matches:
        for m in matches:
            cleaned = m.strip()
            if len(cleaned) >= 20 and not any(bad in cleaned.lower() for bad in ["click here", "read more", "copyright", "subscribe", "terms of"]):
                return cleaned
                
    # If no explicit quotation marks, search for attribution verbs
    quote_verbs = [" said ", " stated ", " declared ", " warned ", " remarked ", " noted ", " added ", " explained ", " asserted "]
    sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 30]
    for s in sentences:
        lower_s = f" {s.lower()} "
        if any(verb in lower_s for verb in quote_verbs) and len(s) <= 240:
            if not any(bad in s.lower() for bad in ["click here", "read more", "copyright", "subscribe"]):
                # Strip the attribution frame (e.g. "The minister warned that ") to extract just the core claim
                cleaned = re.sub(
                    r'^.*?\b(?:said|stated|declared|warned|remarked|noted|added|explained|asserted)\b\s+(?:that\s+)?',
                    '', s, flags=re.IGNORECASE
                ).strip()
                if len(cleaned) >= 20:
                    return cleaned
                return s.strip()
                
    return None

def sanitize_ai_output(text):
    """Content Quality Gateway Guardrail: Trash any output containing CoT preambles,
    meta-analysis bullet lists, prompt leakage (like Alaye/Africa Desk meta references),
    or invalid post structure before publishing to X."""
    if not text:
        return None
        
    # 1. Strip <think>...</think> tags
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    
    # 2. Hard Rejection Triggers: Meta CoT preambles, prompt headers, and rule lists
    meta_patterns = [
        r"(?i)Here'?s\s+a\s+thinking\s+process",
        r"(?i)1\.\s*\*\*Analyze\s+the\s+Request:\*\*",
        r"(?i)\*\*Role:\*\*",
        r"(?i)\*\*Goal:\*\*",
        r"(?i)\*\*Constraints:\*\*",
        r"(?i)\*\*Input:\*\*",
        r"(?i)\*\*Task:\*\*",
        r"(?i)X\s+account\s+[\"']?Alaye",
        r"(?i)Alaye\s*\|\s*Africa\s+Desk",
        r"(?i)Africa\s+Desk",
        r"(?i)originality\s+test",
        r"(?i)author'?s\s+judgment",
        r"(?i)raw\s+facts?",
        r"(?i)credit\s+and\s+link\s+the\s+outlet",
        r"(?i)write\s+posts?\s+for",
        r"(?i)system\s+prompt"
    ]
    
    for pattern in meta_patterns:
        if re.search(pattern, text):
            print(f"[GATEWAY TRASHED] Detected meta/CoT pattern match '{pattern}'. Discarding output!")
            return None

    # 3. Clean up leading markdown formatting if output starts with code fences
    text = re.sub(r'^```[a-zA-Z]*\n', '', text)
    text = re.sub(r'\n```$', '', text).strip()
    
    # 4. Length validation: Must be at least 100 characters of clean prose
    if len(text) < 100:
        print("[GATEWAY TRASHED] Output length too short (<100 chars). Discarding output!")
        return None

    return text

def call_openrouter(title, text, source, author, category, quote=None):
    """Query OpenRouter API to draft original commentary posts in Alaye | Africa Desk persona."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("[Warning] OPENROUTER_API_KEY is not set.")
        return None
        
    model = os.getenv("OPENROUTER_MODEL", "nex-agi/nex-n2.5-mini:free")
    
    quote_rule = f"\nDirect Quote in Article: \"{quote}\"" if quote else ""

    system_prompt = """SYSTEM PROMPT:

You write original news commentary posts for an X news account.

The account is a Nigerian/African commentator. It is not a news wire, not a reprint desk, and not a “rewrite this article” page.

X pays (and reviews) accounts for original commentary. Copied, lightly rewritten, or aggregated news can be rejected. Your job is to make every post pass that test.

Goal of every post
- The post must still make sense if you delete the source article.
- At least 70% of the text is the author’s judgment, comparison, warning, or argument.
- At most 30% is raw fact (who / what / where / how many).
- Always credit and link the outlet. Credit does not make a rewrite original.

Never do
- Rewrite the article in different words and call it a take.
- Copy the reporter’s structure: scene → official quote → death toll → two past examples → “authorities investigating.”
- Paste long official quotes unless one short clause is necessary.
- Use the source’s headline as the post.
- Invent numbers, names, causes, or quotes.
- Add details the source did not confirm.
- Write “Via Outlet | Report by Name” on top of a condensed article. That is a rewrite with a sticker.
- Use words like “tragedy,” “heartbreaking,” “our prayers” as filler.
- Farm engagement: “What do you think?” as the whole point.
- Sound like a press release or a student summary.

Always do
- One or two fact sentences, then the argument.
- Say why it matters to Nigeria / Africa / the reader, if that is honest.
- Make a clear claim someone could disagree with.
- Put the source line at the end.
- If you cannot add a real point, refuse and say: “Not enough for an original post. Only a recap is possible.”

Voice
- Direct. Short sentences. No corporate English.
- Sharp, not cruel. No mockery of victims.
- Sound like a person who reads news and has a view, not like the newspaper.
- Nigerian English is fine when natural. Do not force slang.

Default structure
1. Fact in one or two lines.
2. Your point in 4–8 short sentences.
3. Optional: one comparison you actually understand (not just the two examples already listed in the article).

Source line format
Image Source: [Outlet]

How to use the Direct Quote
If a Direct Quote is provided in the input, you MUST use it. Drop it naturally into your first or second sentence to anchor the post in a real voice from the story. Do not paste it in full as a standalone block. Weave it as a short clause inside your own sentence. Example: the CEO admitted the company "cannot survive without government contracts" which tells you exactly where the real risk sits.

Do not write “Via X | Report by Y”.
Do NOT write the word "Title:" or "Headline:".
Do NOT use any emojis.
Do NOT leave any sentence unfinished or cut off.
Strict Length & Finishing Constraint: Total Response MUST be between 520 and 660 characters total (approx. 1 rich, deep paragraph). Every sentence MUST be completely finished with a period.
Simple English Constraint: Write in clear, simple English that is easy to read on mobile.
ABSOLUTELY NO EMOJIS: Do NOT use any emojis, symbols, or special icons anywhere in your text. Keep it pure text.
Punctuation Constraint: Do not use em dashes (—) or en dashes (–) anywhere. ONLY use standard commas (,) and periods/full stops (.) for punctuation. Do not use exclamation marks (!), question marks (?), colons (:), semicolons (;), or dashes anywhere in your text. If you ask a question at the end, end it with a period.
Complete Sentences Only: Every single sentence MUST be fully written and closed with a period.

- Never thread a recap. Thread only if each post is a new point.

Fact rules
- Use only what the user pasted or what is in the provided article.
- Keep names, places, counts exact.
- If the source says “police said,” do not turn it into settled fact.
- If cause is unknown, say the cause is unknown.
- Do not import extra incidents unless the user supplied them or they are common knowledge the user asked to use.

Originality test before you output
Ask yourself:
1. If I hide the article, is there still an argument?
2. Did I repeat the article’s examples as if they were my insight?
3. Could the original reporter claim I just shortened their piece?

If yes to 2 or 3, rewrite until the answer is no.

Output format
Give ONLY the final commentary post text ready to publish on X (Fact in 1-2 lines, then your point/argument, then Source line). Do NOT output internal extraction, risk assessments, notes, preambles, or rule descriptions. Output ONLY the post text. Nothing else."""

    user_prompt = f"""Input Article:
Title: {title}
Text: {text}
Category: {category}
Source: {source}
Author: {author or 'Unknown'}{quote_rule}

Write the original commentary X post text now:"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://automatewithjohnson.com",
        "X-Title": "AutomatesWithJohnson News Poster"
    }
    
    fallback_models = [
        "nex-agi/nex-n2.5-mini:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "google/gemini-2.0-flash-lite-001",
        "mistralai/mistral-7b-instruct:free",
        "nvidia/nemotron-3.5-lightning:free",
        "poolside/laguna-s-2.1:free",
        "openrouter/free"
    ]
    if model and model not in fallback_models:
        fallback_models.insert(0, model)
    
    for current_model in fallback_models:
        if not current_model:
            continue
        payload = {
            "model": current_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        }
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                if "choices" in data and len(data["choices"]) > 0:
                    raw_text = data["choices"][0]["message"]["content"].strip()
                    clean_text = sanitize_ai_output(raw_text)
                    if clean_text and len(clean_text) > 80:
                        print(f"[SUCCESS] [GATEWAY PASSED] AI text generated via model: {current_model}")
                        return clean_text
                    else:
                        print(f"[WARN] OpenRouter model '{current_model}' output rejected by Gateway (meta/CoT leakage). Switching to fallback...")
            else:
                print(f"[WARN] OpenRouter model '{current_model}' returned HTTP {resp.status_code}: {resp.text[:150]}")
        except Exception as e:
            print(f"[WARN] OpenRouter model '{current_model}' failed: {e}")

    # Completely removed local fallback generator as requested
    print("[ERROR] All OpenRouter models failed or were trashed by Gateway. Returning None.")
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
    media_url, media_type, page_text, fallback_image_url = scrape_webpage(link)
    if not media_url:
        print(f"No media for '{title}'. Proceeding with text news post.")
        
    article_text = page_text if len(page_text) > 50 else description
    
    # Low-value check on scraped body (< 400 chars or filler/ads)
    skip, reason = is_low_value(title, description, article_text)
    if skip:
        print(f"Skipping article '{title}' because it is low-value: {reason}")
        return False

    quote = extract_direct_quote(article_text)
    if quote:
        print(f"Extracted direct quote: '{quote[:70]}...'")
        
    # 2. Get AI tweet text
    tweet_text = call_openrouter(title, article_text, source, author, category, quote=quote)
    if not tweet_text:
        print(f"Skipping article '{title}' because AI text generation failed.")
        return False
        
    # Strip any URLs, existing "Source..." lines, and trailing dashes from raw AI text
    tweet_text = re.sub(r'https?://\S+', '', tweet_text).strip()
    tweet_text = re.sub(r'(?i)\b(Image\s*)?Source:?\s*.*$', '', tweet_text).strip()
    tweet_text = re.sub(r'—\s*$', '', tweet_text).strip()

    source_line = f"Image Source: {source}"
    overhead = len(source_line) + 4  # newlines
    
    max_body_len = max(400, 740 - overhead)
    min_body_len = max(300, 580 - overhead)
    
    # 1. Truncate body if it is too long
    if len(tweet_text) > max_body_len:
        truncated = tweet_text[:max_body_len]
        last_dot = truncated.rfind('.')
        if last_dot > 250:
            tweet_text = truncated[:last_dot + 1].strip()
        else:
            last_space = truncated.rfind(' ')
            if last_space > 200:
                tweet_text = truncated[:last_space].strip() + "."
            else:
                tweet_text = truncated.strip() + "."

    # 2. Pad body from article_text if body is too short (BEFORE appending Source line)
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

    # Strip any trailing source mention again just in case
    tweet_text = re.sub(r'(?i)\b(Image\s*)?Source:?\s*.*$', '', tweet_text).strip()
    tweet_text = re.sub(r'—\s*$', '', tweet_text).strip()

    # Form final tweet: ONLY the clean commentary body + double line break + single Image Source line
    formatted_tweet = f"{tweet_text}\n\n{source_line}"
    
    # Hard safety check: re-trim if total raw post exceeds 745 characters
    if len(formatted_tweet) > 745:
        max_safe_body = 745 - overhead
        truncated = tweet_text[:max_safe_body]
        last_dot = truncated.rfind('.')
        if last_dot > 250:
            tweet_text = truncated[:last_dot + 1].strip()
        else:
            tweet_text = truncated.strip() + "."
        formatted_tweet = f"{tweet_text}\n\n{source_line}"

    # Final Guard: Skip only if formatted length is under 520 chars after padding
    if len(formatted_tweet) < 520:
        print(f"Skipping article '{title}' because total length ({len(formatted_tweet)} chars) is below minimum of 520 chars.")
        return False
        
    print(f"Drafted Tweet ({len(formatted_tweet)} chars):\n{formatted_tweet}")
    
    # 3. Download media (video clip or featured image)
    temp_file_path = download_media(media_url, media_type, fallback_image_url=fallback_image_url)
        
    # Enforce mandatory media attachment for every news post
    if not temp_file_path or not os.path.exists(temp_file_path):
        print(f"Skipping article '{title}' because media could not be downloaded or was not found.")
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
            if temp_file_path.endswith(('.mp4', '.mov', '.avi')):
                media_id = await client.upload_media(temp_file_path, media_category='tweet_video')
            else:
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

    # Parse limit from arguments (defaults to 1 for paced queue execution: 1 post / 10-12 mins)
    limit = 1
    for arg in sys.argv:
        if arg.startswith("--limit="):
            try:
                limit = int(arg.split("=")[1])
            except:
                pass

    # Filter out preliminary low-value items and sort candidates by news impact score
    filtered_articles = []
    for art in new_articles:
        skip, reason = is_low_value(art["title"], art["description"])
        if not skip:
            art["score"] = score_article(art)
            filtered_articles.append(art)
            
    filtered_articles.sort(key=lambda x: x["score"], reverse=True)
    new_articles = filtered_articles

    if not new_articles:
        print("No high-value unposted articles available across all sources.")
        db_conn.close()
        return

    print(f"Ranked {len(new_articles)} high-value candidate articles. Target limit: {limit} post(s)...")
    
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
