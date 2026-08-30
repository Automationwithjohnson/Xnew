import os
import requests
from dotenv import load_dotenv

load_dotenv()
key = os.getenv('OPENROUTER_API_KEY')

MODELS = [
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-chat",
    "google/gemini-2.5-flash",
    "openai/gpt-4o-mini"
]

for model in MODELS:
    print(f"\nTesting model: {model}...")
    try:
        r = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}'},
            json={'model': model, 'messages': [{'role': 'user', 'content': 'Say hello in 5 words'}]},
            timeout=10
        )
        print("Status code:", r.status_code)
        if r.status_code == 200:
            print("Response SUCCESS:", r.json()['choices'][0]['message']['content'])
        else:
            print("Error response:", r.text[:200])
    except Exception as e:
        print("Model failed:", e)
