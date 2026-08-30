import json
import urllib.request
import os

RENDER_API_KEY = "rnd_dF024vXGvzX8J3iDYzjJpVx8dVlN"
SERVICE_ID = "srv-da9v5phf2nfc738nil80"

HEADERS = {
    "Authorization": f"Bearer {RENDER_API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

cookies_path = os.path.join(os.path.dirname(__file__), "Xaccountdata.json")
with open(cookies_path, "r", encoding="utf-8") as f:
    cookies_json_str = f.read()

print("[1] Updating Render Service details...")
update_payload = {
    "name": "newsforx",
    "rootDir": "",
    "serviceDetails": {
        "envSpecificDetails": {
            "buildCommand": "pip install -r requirements.txt",
            "startCommand": "python -u server.py"
        }
    }
}

try:
    req = urllib.request.Request(
        f"https://api.render.com/v1/services/{SERVICE_ID}",
        data=json.dumps(update_payload).encode('utf-8'),
        headers=HEADERS,
        method="PATCH"
    )
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode('utf-8'))
        print("Updated service settings successfully!")
except Exception as e:
    print(f"Error updating service: {e}")

print("\n[2] Setting Environment Variables on Render...")
env_vars_payload = [
    {"key": "PYTHON_VERSION", "value": "3.11.8"},
    {"key": "OPENROUTER_API_KEY", "value": "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"},
    {"key": "X_COOKIES_JSON", "value": cookies_json_str},
    {"key": "OPENROUTER_MODEL", "value": "nvidia/nemotron-3-ultra-550b-a55b:free"},
    {"key": "X_PREMIUM", "value": "true"},
    {"key": "MAX_TWEET_LENGTH", "value": "1500"},
    {"key": "WINDOW_MINUTES", "value": "30"}
]

try:
    req = urllib.request.Request(
        f"https://api.render.com/v1/services/{SERVICE_ID}/env-vars",
        data=json.dumps(env_vars_payload).encode('utf-8'),
        headers=HEADERS,
        method="PUT"
    )
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode('utf-8'))
        print("Updated environment variables successfully!")
except Exception as e:
    print(f"Error updating env vars: {e}")

print("\n[3] Triggering new Deployment on Render...")
try:
    req = urllib.request.Request(
        f"https://api.render.com/v1/services/{SERVICE_ID}/deploys",
        data=json.dumps({"clearCache": "do_not_clear"}).encode('utf-8'),
        headers=HEADERS,
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode('utf-8'))
        print("=== RENDER DEPLOYMENT TRIGGERED ===")
        print(json.dumps(res, indent=2))
except Exception as e:
    print(f"Error triggering deploy: {e}")
