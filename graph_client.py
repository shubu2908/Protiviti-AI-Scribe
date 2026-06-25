"""
GraphChatPoster — posts the MoM into a Teams meeting chat via Microsoft Graph API.
No browser automation involved. Uses MSAL interactive login once; the token is
cached locally so subsequent runs sign in silently.
"""

import logging
from pathlib import Path
from typing import Optional

import msal
import requests

from config import GRAPH_CLIENT_ID, GRAPH_TENANT_ID

logger = logging.getLogger(__name__)

_SCOPES = ["Chat.ReadWrite", "ChatMessage.Send", "OnlineMeetings.Read"]
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
            logger.info("No cached Graph token — opening browser for one-time sign-in...")
            result = self._app.acquire_token_interactive(scopes=_SCOPES)

        self._save_cache()

        if not result or "access_token" not in result:
            err = result.get("error_description") if result else "no response"
            logger.error("Graph authentication failed: %s", err)
            return None
        return result["access_token"]

    def _get_chat_id(self, token: str, join_url: str) -> Optional[str]:
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
        return results[0].get("chatInfo", {}).get("threadId")

    def post_mom(self, join_url: str, message_html: str) -> bool:
        """Post the MoM (as HTML) into the meeting's chat. Returns True on success."""
        if not self.configured:
            return False

        token = self._get_token()
        if not token:
            return False

        chat_id = self._get_chat_id(token, join_url)
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
