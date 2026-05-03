import os

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-1.5-flash"
USE_MOCK = not bool(GEMINI_API_KEY)

GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
