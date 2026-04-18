import os
import requests
from dotenv import load_dotenv

# Load environment
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Change this to your actual PythonAnywhere domain!
DOMAIN = "awoke123.pythonanywhere.com"

if __name__ == "__main__":
    if not TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env")
        exit(1)

    webhook_url = f"https://{DOMAIN}/webhook/{TOKEN}"
    api_url = f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}"

    print(f"Setting webhook to: {webhook_url}...")
    response = requests.get(api_url)
    
    if response.status_code == 200:
        print("Success! Webhook registered.")
        print("Response:", response.json())
    else:
        print(f"Failed to set webhook. Status code: {response.status_code}")
        print("Response:", response.text)
