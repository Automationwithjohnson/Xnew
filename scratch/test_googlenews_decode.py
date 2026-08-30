import base64
import re
import requests

def unwrap_google_news_url(url):
    """Unwrap Google News RSS URL to get the original news publisher URL"""
    if "news.google.com" not in url:
        return url
        
    try:
        # 1. Try decoding the base64 article ID in Google News RSS link
        parts = url.split("articles/")
        if len(parts) > 1:
            article_id = parts[1].split("?")[0]
            # Add padding
            padded_id = article_id + "=" * (-len(article_id) % 4)
            try:
                decoded_bytes = base64.b64decode(padded_id)
                urls = re.findall(r'https?://[^\s"\'<>\x00-\x1f]+', decoded_bytes.decode('latin1', errors='ignore'))
                for u in urls:
                    if "news.google.com" not in u and "." in u:
                        # Clean trailing binary artifacts
                        clean_u = re.sub(r'[\x00-\x1f\x7f-\xff].*$', '', u)
                        print(f"Decoded Google News URL to target: {clean_u}")
                        return clean_u
            except Exception as e:
                print(f"Base64 decode failed: {e}")
                
        # 2. Fallback: follow HTTP redirects with a browser User-Agent
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, allow_redirects=True, timeout=5)
        if resp.url and "google.com" not in resp.url:
            print(f"HTTP redirected Google News URL to target: {resp.url}")
            return resp.url
    except Exception as e:
        print(f"Unwrap failed for {url}: {e}")
        
    return url

test_url = "https://news.google.com/rss/articles/CBMiS2h0dHBzOi8vdHJpYnVuZW9ubGluZW5nLmNvbS9zdWJzaWR5LWNyaXNpcy1kZWVwZW5zLWluLW5pZ2VyaWEtYWRjLWtub2Nrcy15YXJpL9IBAA?oc=5"
print("UNWRAPPED:", unwrap_google_news_url(test_url))
