import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///workout_bot.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
TIMEZONE = "Asia/Almaty"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required in .env file")
