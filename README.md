# Protiviti AI Scribe

An automated Microsoft Teams meeting bot that joins meetings via browser automation,
captures audio with zero-setup using Windows WASAPI loopback, transcribes in real-time
with Gemini 1.5 Flash, and produces a structured Minutes of Meeting (MoM) Markdown file.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10 / 11 | WASAPI loopback is a Windows-only audio API |
| Python 3.10 + | Tested on 3.10, 3.11, 3.12 |
| Gemini API key | Free at <https://aistudio.google.com/app/apikey> |
| Any audio output device | Built-in speakers, headphones, or HDMI — no virtual cable needed |

---

## Installation

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install the Chromium browser for Playwright
python -m playwright install chromium

# 3. Configure your API key
copy .env.example .env
# Then open .env and replace the placeholder with your real Gemini API key
```

---

## Usage

### Option A — Join as an anonymous guest (no Teams account needed)

```bash
python main.py --url "https://teams.microsoft.com/l/meetup-join/..."
```

The bot joins as **"Botverse AI Scribe"** (or whatever you set in `.env`), with microphone
and camera off.  Override the display name in `.env` (`BOT_DISPLAY_NAME=My Scribe`).

### Option B — Join with your saved Teams account session

```bash
# Step 1: Save your session (run once per account)
python main.py --save-session

# Step 2: Join authenticated
python main.py --url "https://teams.microsoft.com/l/meetup-join/..." --mode session
```

### Common flags

| Flag | Description |
|---|---|
| `--url URL` | Teams meeting URL (required unless `--save-session`) |
| `--mode guest\|session` | Guest join (default) or authenticated session join |
| `--title "Q2 Review"` | Meeting title used in the generated MoM |
| `--headless` | Run Chromium without a visible window |
| `--save-session` | Interactive login flow to persist your Teams cookies |

### Examples

```bash
# Headless with a custom title
python main.py --url "https://..." --headless --title "Sprint 42 Retrospective"

# Authenticated join, custom bot name set in .env
python main.py --url "https://..." --mode session --title "Client Discovery Call"
```

---

## How WASAPI loopback works

Standard microphone capture records what the bot's microphone hears.
**WASAPI loopback** is a Windows feature that captures the exact audio stream
being sent to the speaker/headphone output — i.e. the mixed output of Teams, your
system sounds, etc. — without any virtual audio cable or extra software.

```
Teams audio → Windows audio engine → WASAPI loopback → soundcard → WAV chunk
```

The `--mute-audio` Chromium flag prevents the bot's fake audio device from
feeding back into the loopback capture.

---

## Output folder structure

Every run creates a timestamped folder:

```
meeting_output/
└── 20240925_143022/
    ├── chunk_0001_143123.wav   ← 60-second audio segments (non-silent only)
    ├── chunk_0002_143223.wav
    ├── transcript.txt          ← full assembled transcript
    └── mom.md                  ← structured Minutes of Meeting
```

The `transcript.txt` is the raw Gemini output joined in chronological order.
The `mom.md` contains the AI-generated structured document.

---

## Free Tier Limits vs Actual Bot Usage

| Metric | Gemini Free Tier Limit | Typical Bot Usage (1 h meeting) |
|---|---|---|
| Requests per minute (RPM) | 15 | ~1 (one call per 60 s chunk) |
| Tokens per minute (TPM) | 1 000 000 | ~3 000–8 000 |
| Requests per day (RPD) | 1 500 | ~60 chunks + 1 MoM call |
| Context window | 1 M tokens | ~8 000 tokens per call |

A 1-hour meeting uses roughly 62 Gemini calls, well within all free-tier limits.

---

## Troubleshooting

### No audio is captured
- Ensure audio is **playing** before starting the bot — WASAPI loopback only
  captures an active output stream.
- Run `python -c "import soundcard as sc; print(sc.all_speakers())"` to list
  detected devices.
- Check that `soundcard` installed correctly: `pip install soundcard`.

### Bot is stuck in the Teams lobby
- Teams may be prompting for the guest name differently — check
  `debug_join.png` which is saved automatically on failure.
- Try `--mode session` with a saved authenticated session.
- Some meetings require host approval; the bot will wait in the lobby.

### Gemini rate-limit errors (429)
- The bot already paces calls with `TRANSCRIPTION_DELAY = 3 s`.
- If you hit 429s consistently, increase `TRANSCRIPTION_DELAY` in `config.py`
  to `5` or `10`.
- Verify you are using a **free** API key; Pro keys have higher limits.

### CAPTCHA or Microsoft sign-in loop
- Use `--save-session` to persist cookies from a clean manual login.
- Avoid running multiple instances simultaneously — Teams may flag suspicious
  concurrent logins.

### transcript.txt is empty
- Check that chunks were saved in the session output folder.
- Silent meetings produce `[No speech detected]` — verify audio is routing
  through the correct output device.

---

## Architecture

```
main.py (BotverseTeamsBot)
├── browser_bot.py  (TeamsBrowserBot)   — Playwright/Chromium automation
├── audio_capture.py (AudioCapture)     — WASAPI loopback recording thread
├── transcriber.py  (Transcriber)       — per-chunk Gemini transcription
├── mom_generator.py (MoMGenerator)     — full-transcript MoM generation
└── config.py                           — env vars + constants
```

Data flow:
1. `AudioCapture` records 60-second WAV chunks in a background thread.
2. Each chunk triggers `_on_chunk` → `Transcriber.transcribe_chunk`.
3. Transcriber uploads the WAV to Gemini File API, gets text, deletes the upload.
4. On meeting end, `MoMGenerator.generate` receives the full transcript and
   produces the structured Markdown document.

---

## License

Internal tool — Protiviti use only.
