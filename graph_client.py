"""
GraphChatPoster — talks to Microsoft Graph API directly (no browser automation):
  - posts the MoM into a Teams meeting chat
  - fetches the official Teams transcript (real speaker names) when available

Uses MSAL interactive login once; the token is cached locally so subsequent
runs sign in silently.
"""

import logging
import re
import time
from pathlib import Path
from typing import Optional

import msal
import requests

from config import GRAPH_CLIENT_ID, GRAPH_TENANT_ID

logger = logging.getLogger(__name__)

_SCOPES = [
    "Chat.ReadWrite",
    "ChatMessage.Send",
    "OnlineMeetings.Read",
    "OnlineMeetingTranscript.Read.All",
]
_CACHE_FILE = Path(__file__).parent / "graph_token_cache.bin"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphChatPoster:
    def __init__(self) -> None:
        self.configured = bool(GRAPH_CLIENT_ID and GRAPH_TENANT_ID)
        if not self.configured:
            logger.info(
                "Graph API not configured — set GRAPH_CLIENT_ID and GRAPH_TENANT_ID in .env "
                "to enable posting the MoM to Teams chat."
            )
            return

        self._cache = msal.SerializableTokenCache()
        if _CACHE_FILE.exists():
            self._cache.deserialize(_CACHE_FILE.read_text(encoding="utf-8"))

        self._app = msal.PublicClientApplication(
            GRAPH_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}",
            token_cache=self._cache,
        )

    def _save_cache(self) -> None:
        if self._cache.has_state_changed:
            _CACHE_FILE.write_text(self._cache.serialize(), encoding="utf-8")

    def _get_token(self) -> Optional[str]:
        accounts = self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(_SCOPES, account=accounts[0])

        if not result:
            logger.info("No cached Graph token (or new scope added) — opening browser to sign in...")
            result = self._app.acquire_token_interactive(scopes=_SCOPES)

        self._save_cache()

        if not result or "access_token" not in result:
            err = result.get("error_description") if result else "no response"
            logger.error("Graph authentication failed: %s", err)
            return None
        return result["access_token"]

    def _lookup_meeting(self, token: str, join_url: str) -> Optional[dict]:
        """Look up the onlineMeeting object (id, chatInfo, etc.) by its join URL."""
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(
            f"{_GRAPH_BASE}/me/onlineMeetings",
            headers=headers,
            params={"$filter": f"JoinWebUrl eq '{join_url}'"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error("Meeting lookup failed: %s %s", resp.status_code, resp.text[:300])
            return None

        results = resp.json().get("value", [])
        if not results:
            logger.warning(
                "No online meeting found for this URL via Graph API. "
                "This only works for meetings YOU organized."
            )
            return None
        return results[0]

    # ------------------------------------------------------------------
    # Chat posting
    # ------------------------------------------------------------------

    def post_mom(self, join_url: str, message_html: str) -> bool:
        """Post the MoM (as HTML) into the meeting's chat. Returns True on success."""
        if not self.configured:
            return False

        token = self._get_token()
        if not token:
            return False

        meeting = self._lookup_meeting(token, join_url)
        if not meeting:
            return False
        chat_id = meeting.get("chatInfo", {}).get("threadId")
        if not chat_id:
            return False

        resp = requests.post(
            f"{_GRAPH_BASE}/chats/{chat_id}/messages",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"body": {"contentType": "html", "content": message_html}},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info("MoM posted to Teams chat via Graph API")
            return True

        logger.error("Graph chat post failed: %s %s", resp.status_code, resp.text[:300])
        return False

    # ------------------------------------------------------------------
    # Official Teams transcript (real speaker names)
    # ------------------------------------------------------------------

    def get_transcript(
        self, join_url: str, max_wait_seconds: int = 60, poll_interval: int = 15
    ) -> Optional[str]:
        """Fetch the official Teams transcript (with real speaker names) for a meeting,
        if transcription was started during the call. Polls briefly since transcripts
        take a short time to process after the meeting ends. Returns None if
        transcription was never started or isn't ready within the wait window."""
        if not self.configured:
            return None

        token = self._get_token()
        if not token:
            return None

        meeting = self._lookup_meeting(token, join_url)
        if not meeting:
            return None
        meeting_id = meeting.get("id")
        headers = {"Authorization": f"Bearer {token}"}

        waited = 0
        while True:
            resp = requests.get(
                f"{_GRAPH_BASE}/me/onlineMeetings/{meeting_id}/transcripts",
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                transcripts = resp.json().get("value", [])
                if transcripts:
                    transcript_id = transcripts[-1]["id"]
                    content_resp = requests.get(
                        f"{_GRAPH_BASE}/me/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content",
                        headers={**headers, "Accept": "text/vtt"},
                        timeout=20,
                    )
                    if content_resp.status_code == 200:
                        logger.info("Official Teams transcript retrieved (real speaker names)")
                        return self._parse_vtt(content_resp.text)
                    logger.warning("Transcript content fetch failed: %s", content_resp.status_code)
            elif resp.status_code != 404:
                logger.warning("Transcript list fetch failed: %s %s", resp.status_code, resp.text[:200])

            if waited >= max_wait_seconds:
                break
            time.sleep(poll_interval)
            waited += poll_interval

        logger.info(
            "No official Teams transcript available (transcription likely wasn't "
            "started during the meeting) — falling back to live audio transcription"
        )
        return None

    @staticmethod
    def _parse_vtt(vtt_text: str) -> str:
        """Convert WebVTT (Teams format, <v Speaker Name>text</v> cues) into 'Name: text' lines."""
        pattern = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>)?$")
        lines: list[str] = []
        for raw_line in vtt_text.splitlines():
            raw_line = raw_line.strip()
            if not raw_line or raw_line == "WEBVTT" or "-->" in raw_line or raw_line.isdigit():
                continue
            m = pattern.match(raw_line)
            if m:
                speaker = m.group(1).strip()
                text = re.sub(r"</?v[^>]*>", "", m.group(2)).strip()
                if text:
                    lines.append(f"{speaker}: {text}")
        return "\n\n".join(lines)
