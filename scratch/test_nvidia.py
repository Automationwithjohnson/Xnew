import os
import requests
from dotenv import load_dotenv

load_dotenv()
key = os.getenv('OPENROUTER_API_KEY')
model = "nvidia/nemotron-3-ultra-550b-a55b:free"
print(f"Testing model {model}...")

try:
    r = requests.post(
        'https://openrouter.ai/api/v1/chat/completions',
        headers={'Authorization': f'Bearer {key}'},
        json={'model': model, 'messages': [{'role': 'user', 'content': 'Write a 1-sentence comment about technology.'}]},
        timeout=10
    )
    print("Status:", r.status_code)
    print("Response:", r.text[:300])
except Exception as e:
    print("Error:", e)
