import asyncio
import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from config import BOT_DISPLAY_NAME

logger = logging.getLogger(__name__)

SESSION_FILE = "teams_session.json"

_LAUNCH_ARGS = [
    "--use-fake-ui-for-media-stream",       # auto-accept mic/camera permission dialogs
    "--use-fake-device-for-media-stream",   # use silent fake mic/camera so Chrome never grabs the real mic
                                            # (without this Windows routes the real mic to speakers = echo)
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--mute-audio",  # Chrome stays silent — loopback captures Teams desktop app audio instead
    "--disable-infobars",
    "--disable-extensions",
    "--disable-popup-blocking",
    "--disable-notifications",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


class TeamsBrowserBot:
    def __init__(self) -> None:
        self.display_name: str = BOT_DISPLAY_NAME
        self.is_in_meeting: bool = False
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def launch(self, headless: bool = False, use_saved_session: bool = False) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=headless,
            args=_LAUNCH_ARGS,
        )

        context_kwargs: dict = {
            "user_agent": _USER_AGENT,
            "permissions": ["microphone", "camera"],
            "viewport": {"width": 1280, "height": 800},
        }

        if use_saved_session and Path(SESSION_FILE).exists():
            with open(SESSION_FILE, "r", encoding="utf-8") as fh:
                storage_state = json.load(fh)
            context_kwargs["storage_state"] = storage_state
            logger.info("Loaded saved Teams session from %s", SESSION_FILE)

        self._context = await self._browser.new_context(**context_kwargs)
        self._page = await self._context.new_page()
        logger.info("Browser launched (headless=%s)", headless)

    async def save_session(self) -> None:
        if not self._context:
            raise RuntimeError("Browser context is not initialised — call launch() first.")
        state = await self._context.storage_state()
        with open(SESSION_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        logger.info("Teams session saved to %s", SESSION_FILE)

    async def interactive_login_and_save(self) -> None:
        if not self._page:
            raise RuntimeError("Page is not initialised — call launch() first.")
        await self._page.goto("https://teams.microsoft.com")
        print("\n" + "=" * 60)
        print("ACTION REQUIRED")
        print("=" * 60)
        print("A browser window has opened with Microsoft Teams.")
        print("Please log in with your Microsoft account manually.")
        print("Once you have fully signed in and Teams has loaded,")
        print("come back here and press Enter to save your session.")
        print("=" * 60 + "\n")
        input("Press Enter once you are logged in to Teams… ")
        await self.save_session()
        print("Session saved successfully.")

    # ------------------------------------------------------------------
    # Joining a meeting
    # ------------------------------------------------------------------

    async def join_as_guest(self, meeting_url: str) -> None:
        await self._page.goto(meeting_url)
        await self._dismiss_app_dialog()
        # Give the pre-join page time to fully render before interacting
        await asyncio.sleep(4)
        await self.screenshot("debug_prejoin.png")  # saved for selector debugging
        await self._enter_guest_name()
        await self._ensure_av_off()
        # Brief pause so user can see the pre-join state before bot clicks Join
        await asyncio.sleep(3)
        await self._click_join()
        await asyncio.sleep(6)
        self.is_in_meeting = True
        logger.info("Joined meeting as guest: %s", self.display_name)

    async def join_with_saved_session(self, meeting_url: str) -> None:
        await self._page.goto(meeting_url)
        await self._dismiss_app_dialog()
        await self._ensure_av_off()
        await self._click_join()
        await asyncio.sleep(6)
        self.is_in_meeting = True
        logger.info("Joined meeting with saved session")

    # ------------------------------------------------------------------
    # Pre-join helpers
    # ------------------------------------------------------------------

    async def _dismiss_app_dialog(self) -> None:
        selectors = [
            "text=Continue on this browser",
            "text=Join on this browser",
            "text=Use the web app instead",
            "[data-tid='joinOnWeb']",
        ]
        for sel in selectors:
            try:
                await self._page.wait_for_selector(sel, timeout=5000)
                await self._page.click(sel)
                logger.info("Dismissed app dialog via selector: %s", sel)
                await asyncio.sleep(1)
                return
            except Exception:
                continue
        logger.debug("No app-download dialog found (may already be on web client)")

    async def _enter_guest_name(self) -> None:
        # Wait for the pre-join page to fully render before looking for the name field
        await asyncio.sleep(3)
        selectors = [
            "[data-tid='prejoin-display-name-input']",
            "input[placeholder*='name' i]",
            "input[placeholder*='Enter' i]",
            "input[aria-label*='name' i]",
            "input[aria-label*='your name' i]",
            "#username-input",
            "[data-tid='anonymous-join-name-input']",
            "input[type='text']",
        ]
        for sel in selectors:
            try:
                await self._page.wait_for_selector(sel, timeout=15000)
                await self._page.triple_click(sel)
                await self._page.fill(sel, self.display_name)
                await asyncio.sleep(0.5)
                logger.info("Entered guest name '%s' via selector: %s", self.display_name, sel)
                return
            except Exception:
                continue
        logger.warning("Could not find name-input field; joining without entering name")

    async def _ensure_av_off(self) -> None:
        """Guarantee mic is muted and camera is off before joining. Tries all known selectors."""
        await asyncio.sleep(2)  # Let pre-join controls render fully

        # --- Mute microphone ---
        mic_selectors = [
            "[data-tid='toggle-mute']",
            "button[aria-label*='Mute' i]",
            "button[aria-label*='Microphone' i]",
            "button[title*='Mute' i]",
            "button[title*='Microphone' i]",
        ]
        mic_done = False
        for sel in mic_selectors:
            try:
                elem = await self._page.wait_for_selector(sel, timeout=5000)
                pressed = await elem.get_attribute("aria-pressed")
                label = (await elem.get_attribute("aria-label") or "").lower()
                # pressed="false" → mic is ON → click to mute
                # "unmute" in label → mic is already muted (button says "click to unmute") → skip
                if pressed == "false" and "unmute" not in label:
                    await elem.click()
                    await asyncio.sleep(0.3)
                    logger.info("Microphone muted")
                else:
                    logger.info("Microphone already off")
                mic_done = True
                break
            except Exception:
                continue
        if not mic_done:
            logger.warning("Could not find mic toggle — bot may join with mic active")

        await asyncio.sleep(0.5)

        # --- Turn off camera ---
        cam_selectors = [
            "[data-tid='toggle-video']",
            "button[aria-label*='Camera' i]",
            "button[aria-label*='Video' i]",
            "button[aria-label*='Stop video' i]",
            "button[aria-label*='Turn off camera' i]",
            "button[title*='Camera' i]",
            "button[title*='Video' i]",
        ]
        cam_done = False
        for sel in cam_selectors:
            try:
                elem = await self._page.wait_for_selector(sel, timeout=5000)
                pressed = await elem.get_attribute("aria-pressed")
                label = (await elem.get_attribute("aria-label") or "").lower()
                # pressed="true" → camera is ON → click to turn off
                # "turn on" in label → camera is already off → skip
                if pressed == "true" and "turn on" not in label:
                    await elem.click()
                    await asyncio.sleep(0.3)
                    logger.info("Camera turned off")
                else:
                    logger.info("Camera already off")
                cam_done = True
                break
            except Exception:
                continue

        # Last resort: scan all buttons for any camera/video related one that appears active
        if not cam_done:
            try:
                all_buttons = await self._page.query_selector_all("button")
                for btn in all_buttons:
                    label = (await btn.get_attribute("aria-label") or "").lower()
                    pressed = await btn.get_attribute("aria-pressed")
                    if any(k in label for k in ["camera", "video"]):
                        logger.info("Found camera button via scan: label='%s' pressed='%s'", label, pressed)
                        if pressed == "true":
                            await btn.click()
                            await asyncio.sleep(0.3)
                            logger.info("Camera turned off via button scan")
                            cam_done = True
                            break
            except Exception as exc:
                logger.warning("Button scan failed: %s", exc)

        if not cam_done:
            await self.screenshot("debug_camera.png")
            logger.warning("Could not find camera toggle — check debug_camera.png")

    async def _click_join(self) -> None:
        selectors = [
            "[data-tid='prejoin-join-button']",
            "button:has-text('Join now')",
            "button:has-text('Ask to join')",
            "button:has-text('Join')",
        ]
        for sel in selectors:
            try:
                await self._page.wait_for_selector(sel, timeout=8000)
                await self._page.click(sel)
                logger.info("Clicked join button via selector: %s", sel)
                return
            except Exception:
                continue
        logger.error("Could not find join button — saving debug screenshot")
        await self.screenshot("debug_join.png")

    # ------------------------------------------------------------------
    # In-meeting helpers
    # ------------------------------------------------------------------

    async def extract_participants(self) -> tuple[list[str], str]:
        """Return (participant_names, organizer_name). organizer_name is '' if not found."""
        roster_selectors = [
            "button[aria-label*='Participants' i]",
            "button[aria-label*='People' i]",
            "[data-tid='roster-button']",
        ]
        for sel in roster_selectors:
            try:
                await self._page.click(sel, timeout=5000)
                await asyncio.sleep(1)
                break
            except Exception:
                continue

        name_selectors = [
            "[data-tid*='participant-name']",
            "[class*='participantName']",
        ]
        participants: list[str] = []
        for sel in name_selectors:
            try:
                elements = await self._page.query_selector_all(sel)
                participants = [((await e.text_content()) or "").strip() for e in elements]
                participants = [p for p in participants if p]
                if participants:
                    break
            except Exception:
                continue

        # Try to find the organizer (labelled "Organiser" or "Organizer" in Teams)
        organizer = ""
        organizer_selectors = [
            "[class*='participantItem']:has-text('Organis')",
            "[data-tid*='participant']:has-text('Organis')",
        ]
        for sel in organizer_selectors:
            try:
                items = await self._page.query_selector_all(sel)
                for item in items:
                    name_el = await item.query_selector("[data-tid*='participant-name'], [class*='participantName']")
                    if name_el:
                        organizer = ((await name_el.text_content()) or "").strip()
                        break
                if organizer:
                    break
            except Exception:
                continue

        logger.info("Participants: %s | Organizer: %s", participants, organizer or "unknown")
        return participants, organizer

    async def keep_alive(self) -> None:
        """Periodically move the mouse to prevent the browser from going idle."""
        while self.is_in_meeting:
            try:
                await self._page.mouse.move(640 + 5, 400)
                await asyncio.sleep(1)
                await self._page.mouse.move(640 - 5, 400)
            except Exception:
                pass
            await asyncio.sleep(30)

    async def wait_for_meeting_end(self, poll_interval: int = 15) -> None:
        end_selectors = [
            "text=The meeting has ended",
            "text=Meeting ended",
            "text=This meeting has ended",
            "text=This call has ended",
            "text=Call ended",
            "[data-tid='meeting-ended-banner']",
            "[data-tid='call-ended-banner']",
            "text=You left the meeting",
            "text=You've left the meeting",
            # Light meeting (meet/ URLs) — shows Rejoin when host ends
            "button:has-text('Rejoin')",
            "text=Return to home",
            "text=Go back",
        ]
        meeting_url_fragment = self._page.url
        logger.info("Watching for meeting-end signals (poll every %ds)…", poll_interval)
        while True:
            # Check text/element selectors
            for sel in end_selectors:
                try:
                    await self._page.wait_for_selector(sel, timeout=poll_interval * 1000)
                    logger.info("Meeting-end detected via selector: %s", sel)
                    self.is_in_meeting = False
                    return
                except Exception:
                    continue

            # Check if page navigated away from the meeting (URL changed significantly)
            try:
                current_url = self._page.url
                if current_url and meeting_url_fragment:
                    # If we've been redirected away from the meeting page entirely
                    in_meeting_url = any(kw in current_url for kw in [
                        "meet/", "meetup-join", "light-meetings/launch", "light-meetings/meeting"
                    ])
                    if not in_meeting_url and "teams.microsoft.com" in current_url:
                        logger.info("Meeting-end detected via URL change: %s", current_url)
                        self.is_in_meeting = False
                        return
            except Exception:
                pass

            await asyncio.sleep(poll_interval)

    async def leave_meeting(self) -> None:
        leave_selectors = [
            "[data-tid='hangup-main-btn']",
            "button[aria-label*='Leave' i]",
        ]
        for sel in leave_selectors:
            try:
                await self._page.click(sel, timeout=5000)
                await asyncio.sleep(1)
                break
            except Exception:
                continue

        # Confirm leave dialog if it appears
        confirm_selectors = [
            "button:has-text('Leave')",
            "button:has-text('Leave meeting')",
            "[data-tid='hangup-confirm-btn']",
        ]
        for sel in confirm_selectors:
            try:
                await self._page.click(sel, timeout=3000)
                break
            except Exception:
                continue

        self.is_in_meeting = False
        logger.info("Left the meeting")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    async def post_to_meeting_chat(self, message: str) -> bool:
        """Post a message to the Teams meeting chat. Returns True on success."""
        if not self._page:
            return False

        # Open the chat panel
        chat_btn_selectors = [
            "button[aria-label*='Chat' i]",
            "[data-tid='chat-button']",
            "[data-tid='callingButtons-showChatButton']",
            "button:has-text('Chat')",
        ]
        for sel in chat_btn_selectors:
            try:
                await self._page.click(sel, timeout=4000)
                await asyncio.sleep(2)
                logger.info("Chat panel opened via: %s", sel)
                break
            except Exception:
                continue

        # Find the message input box
        input_selectors = [
            "div[contenteditable='true'][aria-label*='message' i]",
            "div[contenteditable='true'][role='textbox']",
            "[data-tid='messageInputField']",
            "div[class*='ql-editor']",
            "div[contenteditable='true']",
        ]
        for sel in input_selectors:
            try:
                box = await self._page.wait_for_selector(sel, timeout=8000)
                await box.click()
                await asyncio.sleep(0.5)

                # Use clipboard to paste multi-line text (avoids Enter-sends-message issue)
                import json
                await self._page.evaluate(
                    f"navigator.clipboard.writeText({json.dumps(message)})"
                )
                await self._page.keyboard.press("Control+v")
                await asyncio.sleep(1)

                # Send with Ctrl+Enter (works in Teams web regardless of Enter key setting)
                await self._page.keyboard.press("Control+Enter")
                await asyncio.sleep(1)
                logger.info("MoM posted to Teams meeting chat (%d chars)", len(message))
                return True
            except Exception as exc:
                logger.warning("Chat input %s failed: %s", sel, exc)
                continue

        logger.error("Could not post MoM to Teams chat — no input box found")
        await self.screenshot("debug_chat.png")
        return False

    async def screenshot(self, path: str = "debug.png") -> None:
        try:
            await self._page.screenshot(path=path)
            logger.info("Screenshot saved: %s", path)
        except Exception as exc:
            logger.warning("Could not take screenshot: %s", exc)

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.warning("Error closing browser: %s", exc)
        logger.info("Browser closed")
