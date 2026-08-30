import requests
import re

url = "https://news.google.com/rss/articles/CBMixgFBVV_5cUxPdFJjbWk3TGRGWWh4dFJ2NjZEY1VMdmh4YlMtSmYyUUlEY3lmZGFEWVV6LU00dFlSMVdCTURQUGRqWkpJV2RCX2lZaXNUYjZKZWtndWZiRnFCemxKTW1rTWZaRW1UT2U5N3BIT1NDY2IzSzRSZnpHcnJmVWEwN3V5dEt6Ukw0aUNYVWRQeDJpNlp0TTFnSWtLNDdwdVJjYk5lUlNUVVZwNlNzcDBXaXFTR2RuQkx5UlRicUtEQmtTWmktNjBwUXc?oc=5"
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

resp = requests.get(url, headers=headers)
print("STATUS:", resp.status_code)
print("FINAL URL:", resp.url)

# Search for any real publisher domain in resp.text
urls = re.findall(r'https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/[^\s"\'<>]+', resp.text)
non_google = [u for u in urls if not any(g in u for g in ["google", "gstatic", "schema.org", "w3.org"])]
print("FOUND NON-GOOGLE URLS:", non_google[:5])
