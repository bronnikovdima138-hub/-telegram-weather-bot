import os
from dotenv import load_dotenv, find_dotenv

# 1) Explicitly load .env from this file's directory (project root)
here = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(here, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path, override=True)
else:
    # 2) Try to locate .env via find_dotenv using current working directory
    found = find_dotenv(filename=".env", raise_error_if_not_found=False, usecwd=True)
    if found:
        load_dotenv(dotenv_path=found, override=True)
    else:
        # 3) Fallback to default search
        load_dotenv(override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # e.g. https://your-app.onrender.com/
PORT = int(os.getenv("PORT", "8000"))

if not TELEGRAM_BOT_TOKEN:
    # Manual fallback: read .env and extract the token
    try:
        # Try module dir first
        env_file = os.path.join(here, ".env")
        if not os.path.exists(env_file):
            env_file = os.path.join(os.getcwd(), ".env")
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Remove UTF-8 BOM if present
                    if line and line[0] == "\ufeff":
                        line = line.lstrip("\ufeff")
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        TELEGRAM_BOT_TOKEN = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if TELEGRAM_BOT_TOKEN:
                            os.environ["TELEGRAM_BOT_TOKEN"] = TELEGRAM_BOT_TOKEN
                        break
    except Exception:
        pass

