import logging
import time
from pathlib import Path

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, TRANSCRIPTION_DELAY

logger = logging.getLogger(__name__)

_TRANSCRIPTION_PROMPT = (
    "You are an expert transcription assistant. Transcribe the following audio recording accurately.\n"
    "- Identify different speakers and label them as 'Speaker 1:', 'Speaker 2:', etc.\n"
    "- Preserve all technical terms, product names, and proper nouns exactly as spoken.\n"
    "- Include filler words only if they are meaningful; remove excessive 'um', 'uh'.\n"
    "- If the audio contains no speech (silence, background noise only), reply with exactly: [SILENCE]\n"
    "- Output plain text only — no markdown formatting, no commentary, no preamble.\n"
    "Transcribe now:"
)


class Transcriber:
    def __init__(self) -> None:
        if not GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not set. "
                "Copy .env.example to .env and add your key from https://aistudio.google.com/app/apikey"
            )
        self._client = genai.Client(api_key=GEMINI_API_KEY)
        self._parts: list[dict] = []  # list of {"chunk": int, "text": str}

    # ------------------------------------------------------------------

    def transcribe_chunk(self, audio_path: str, chunk_index: int) -> str:
        """Upload a WAV chunk to Gemini and return the transcription text."""
        uploaded = None
        try:
            logger.info("Uploading chunk %04d: %s", chunk_index, audio_path)
            uploaded = self._client.files.upload(
                file=audio_path,
                config=types.UploadFileConfig(mime_type="audio/wav"),
            )
            # Brief pause to let the File API finish processing
            time.sleep(2)

            response = self._client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[uploaded, _TRANSCRIPTION_PROMPT],
                config=types.GenerateContentConfig(temperature=0),
            )
            text: str = response.text.strip() if response.text else ""

            if text and text != "[SILENCE]":
                self._parts.append({"chunk": chunk_index, "text": text})
                logger.info("Chunk %04d transcribed (%d chars)", chunk_index, len(text))
            else:
                logger.debug("Chunk %04d: %s", chunk_index, text or "(empty response)")
                text = ""

            time.sleep(TRANSCRIPTION_DELAY)
            return text

        except Exception as exc:
            logger.error("Transcription failed for chunk %04d: %s", chunk_index, exc)
            time.sleep(10)  # back-off before next call
            return ""

        finally:
            if uploaded is not None:
                try:
                    self._client.files.delete(name=uploaded.name)
                    logger.debug("Deleted uploaded file for chunk %04d", chunk_index)
                except Exception as exc:
                    logger.warning("Could not delete uploaded file: %s", exc)

    # ------------------------------------------------------------------

    def get_full_transcript(self) -> str:
        """Return the complete transcript sorted by chunk index."""
        sorted_parts = sorted(self._parts, key=lambda p: p["chunk"])
        return "\n\n".join(p["text"] for p in sorted_parts)

    def save_transcript(self, output_path: str) -> str:
        """Write the full transcript to a file and return the text."""
        text = self.get_full_transcript()
        Path(output_path).write_text(text, encoding="utf-8")
        logger.info("Transcript saved to %s", output_path)
        return text
