import feedparser
import requests
import bs4

test_feeds = [
    ("Daily Post Nigeria", "https://dailypost.ng/feed/"),
    ("Sahara Reporters", "https://saharareporters.com/feeder"),
    ("Vanguard News", "https://www.vanguardngr.com/feed/"),
    ("Nairametrics", "https://nairametrics.com/feed/"),
    ("Premium Times", "https://www.premiumtimesng.com/feed"),
    ("Complete Sports", "https://www.completesports.com/feed/")
]

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

for name, feed_url in test_feeds:
    print(f"\n--- Testing Feed: {name} ({feed_url}) ---")
    try:
        resp = requests.get(feed_url, headers=headers, timeout=5)
        feed = feedparser.parse(resp.content)
        print(f"Items found: {len(feed.entries)}")
        if feed.entries:
            first = feed.entries[0]
            print(f"Title: {first.title}")
            print(f"Link: {first.link}")
            
            # Scrape og:image directly from publisher link
            res_art = requests.get(first.link, headers=headers, timeout=5)
            soup = bs4.BeautifulSoup(res_art.text, "html.parser")
            og_img = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
            img_url = og_img["content"] if og_img else "No Image Found"
            print(f"Direct Article Image: {img_url}")
    except Exception as e:
        print(f"Error testing feed {name}: {e}")
