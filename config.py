import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
BOT_DISPLAY_NAME: str = os.getenv("BOT_DISPLAY_NAME", "Botverse AI Scribe")

GEMINI_MODEL = "gemini-2.5-flash"

# Email delivery (all optional — skip if not set)
SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM: str = os.getenv("EMAIL_FROM", "") or os.getenv("SMTP_USER", "")
EMAIL_TO: str = os.getenv("EMAIL_TO", "")  # comma-separated recipients
AUDIO_CHUNK_DURATION = 60          # seconds per recorded chunk
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
SILENCE_RMS_THRESHOLD = 0.005
MAX_MEETING_DURATION = 3 * 60 * 60  # 3 hours in seconds
OUTPUT_DIR = "meeting_output"
TRANSCRIPTION_DELAY = 3            # seconds between Gemini API calls (free-tier pacing)
