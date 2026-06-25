"""
Protiviti AI Scribe — main entry point.
Joins a Microsoft Teams meeting, records audio via WASAPI loopback,
transcribes in real-time with Gemini 1.5 Flash, and generates a MoM.
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from config import GEMINI_API_KEY, MAX_MEETING_DURATION, OUTPUT_DIR, EMAIL_TO, ORGANIZER_EMAIL
from audio_capture import AudioCapture
from browser_bot import TeamsBrowserBot
from mom_generator import MoMGenerator
from transcriber import Transcriber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


class BotverseTeamsBot:
    def __init__(self) -> None:
        if not GEMINI_API_KEY:
            print(
                "\n[ERROR] GEMINI_API_KEY is not set.\n"
                "1. Copy .env.example to .env\n"
                "2. Add your key from https://aistudio.google.com/app/apikey\n"
                "3. Re-run the bot.\n"
            )
            raise SystemExit(1)

        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = Path(OUTPUT_DIR) / self.session_id
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Session output directory: %s", self.output_dir)

        self.browser = TeamsBrowserBot()
        self.audio = AudioCapture(output_dir=str(self.output_dir))
        self.transcriber = Transcriber()
        self.mom_gen = MoMGenerator()
        self._participants: list[str] = []
        self._organizer: str = ""

    # ------------------------------------------------------------------
    # Audio callback — called from the audio thread
    # ------------------------------------------------------------------

    def _on_chunk(self, chunk_path: str, chunk_index: int) -> None:
        """Transcribe each audio chunk as it arrives (called from the recording thread)."""
        self.transcriber.transcribe_chunk(chunk_path, chunk_index)

    # ------------------------------------------------------------------
    # Session-save flow
    # ------------------------------------------------------------------

    def save_session_flow(self) -> None:
        """Interactive: open browser, let the user log in, save session cookies."""
        asyncio.run(self._save_session_async())

    async def _save_session_async(self) -> None:
        await self.browser.launch(headless=False)
        try:
            await self.browser.interactive_login_and_save()
        finally:
            await self.browser.close()

    # ------------------------------------------------------------------
    # Main meeting flow
    # ------------------------------------------------------------------

    async def run(
        self,
        meeting_url: str,
        mode: str = "guest",
        meeting_title: str = "Teams Meeting",
        headless: bool = False,
    ) -> None:
        # 1. Launch browser
        use_session = mode == "session"
        await self.browser.launch(headless=headless, use_saved_session=use_session)

        try:
            # 2. Join the meeting
            if use_session:
                await self.browser.join_with_saved_session(meeting_url)
            else:
                await self.browser.join_as_guest(meeting_url)

            # 3. Extract participants
            await asyncio.sleep(10)
            self._participants, self._organizer = await self.browser.extract_participants()
            if self._participants:
                logger.info("Participants: %s", ", ".join(self._participants))
            if self._organizer:
                logger.info("Organizer: %s", self._organizer)

            # 4. Start audio recording
            self.audio.start(callback=self._on_chunk)

            # 5. Keep the browser alive in a background task
            keep_alive_task = asyncio.create_task(self.browser.keep_alive())

            # 6. Wait for meeting end (or timeout / keyboard interrupt)
            logger.info("Waiting for meeting to end (max %dh)…", MAX_MEETING_DURATION // 3600)
            try:
                await asyncio.wait_for(
                    self.browser.wait_for_meeting_end(),
                    timeout=MAX_MEETING_DURATION,
                )
                logger.info("Meeting ended naturally")
            except asyncio.TimeoutError:
                logger.warning("Maximum meeting duration reached (%dh) — leaving", MAX_MEETING_DURATION // 3600)
            except (KeyboardInterrupt, asyncio.CancelledError):
                logger.info("Interrupted by user — leaving meeting")

            # 7. Cancel keep-alive
            keep_alive_task.cancel()
            try:
                await keep_alive_task
            except asyncio.CancelledError:
                pass

        finally:
            # Stop recording first
            self.audio.stop()
            # Generate outputs BEFORE leaving so we can post to Teams chat
            # while the browser is still inside the meeting room
            try:
                await self._generate_outputs(meeting_title, self._participants, self._organizer)
            except Exception as exc:
                logger.error("Output generation error: %s", exc)
            # Now leave and close
            await self.browser.leave_meeting()
            await self.browser.close()

    # ------------------------------------------------------------------
    # Output generation
    # ------------------------------------------------------------------

    async def _generate_outputs(self, meeting_title: str, participants: list[str], organizer: str = "") -> None:
        transcript = self.transcriber.get_full_transcript()
        transcript_path = str(self.output_dir / "transcript.txt")

        # Always write transcript file
        if transcript.strip():
            Path(transcript_path).write_text(transcript, encoding="utf-8")
        else:
            Path(transcript_path).write_text("[No speech detected]", encoding="utf-8")
            transcript = ""

        mom_path = str(self.output_dir / "mom.md")
        mom_text = self.mom_gen.generate(transcript, meeting_title, participants)
        self.mom_gen.save(mom_text, mom_path)

        # Email the MoM — build recipient list from EMAIL_TO + ORGANIZER_EMAIL
        email_status = ""
        recipients_set: set[str] = set()
        if EMAIL_TO:
            recipients_set.update(r.strip() for r in EMAIL_TO.split(",") if r.strip())
        if ORGANIZER_EMAIL:
            recipients_set.add(ORGANIZER_EMAIL.strip())
        # If organizer detected from Teams and matches a configured domain, note it
        if organizer and not ORGANIZER_EMAIL:
            logger.info("Organizer detected as '%s' — add ORGANIZER_EMAIL to .env to email them", organizer)

        if recipients_set:
            from email_sender import EmailSender
            import os
            from config import SMTP_USER, SMTP_PASSWORD
            if SMTP_USER and SMTP_PASSWORD:
                # Temporarily override EMAIL_TO with the full recipient set
                os.environ["EMAIL_TO"] = ",".join(recipients_set)
                # Reload config value
                import importlib, config as _cfg
                importlib.reload(_cfg)
                sender = EmailSender()
                sent = sender.send_mom(mom_text, mom_path, meeting_title, participants)
                email_status = ", ".join(recipients_set) if sent else "failed (check log)"

        # Post to Teams meeting chat
        chat_status = ""
        if mom_text:
            from mom_generator import MoMGenerator
            chat_msg = MoMGenerator.format_for_teams_chat(mom_text, meeting_title)
            posted = await self.browser.post_to_meeting_chat(chat_msg)
            chat_status = "posted" if posted else "failed (check log)"

        # --- Summary printout ---
        print("\n" + "=" * 60)
        print("SESSION COMPLETE")
        print("=" * 60)
        print(f"  Output folder : {self.output_dir}")
        print(f"  Transcript    : {transcript_path}")
        print(f"  Minutes (MoM) : {mom_path}")
        if chat_status:
            print(f"  Teams chat    : {chat_status}")
        if email_status:
            print(f"  Email sent to : {email_status}")
        print("=" * 60)
        if mom_text:
            preview = mom_text[:800]
            print("\n--- MoM Preview (first 800 chars) ---\n")
            print(preview)
            if len(mom_text) > 800:
                print("… [truncated — open mom.md for the full document]")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="protiviti-scribe",
        description="Protiviti AI Scribe — automated Teams meeting recorder & MoM generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quick-start examples:
  # Join as anonymous guest
  python main.py --url "https://teams.microsoft.com/l/meetup-join/..."

  # Save your Teams login session (run once)
  python main.py --save-session

  # Join with saved session (authenticated)
  python main.py --url "https://teams.microsoft.com/l/meetup-join/..." --mode session

  # Run headless (no visible browser window)
  python main.py --url "https://..." --headless

  # Custom meeting title in the MoM
  python main.py --url "https://..." --title "Q2 Strategy Review"
""",
    )
    parser.add_argument("--url", help="Teams meeting URL to join")
    parser.add_argument(
        "--mode",
        choices=["guest", "session"],
        default="guest",
        help="Join as guest (no sign-in) or with a saved Teams session (default: guest)",
    )
    parser.add_argument(
        "--title",
        default="Teams Meeting",
        help="Meeting title used in the MoM document (default: 'Teams Meeting')",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (no visible window)",
    )
    parser.add_argument(
        "--save-session",
        dest="save_session",
        action="store_true",
        help="Open browser for manual Teams login and save the session for future use",
    )

    args = parser.parse_args()

    bot = BotverseTeamsBot()

    if args.save_session:
        bot.save_session_flow()
    elif args.url:
        asyncio.run(
            bot.run(
                meeting_url=args.url,
                mode=args.mode,
                meeting_title=args.title,
                headless=args.headless,
            )
        )
    else:
        parser.print_help()
        print("\nExamples:")
        print('  python main.py --url "https://teams.microsoft.com/l/meetup-join/..."')
        print("  python main.py --save-session")
        sys.exit(1)


if __name__ == "__main__":
    main()
