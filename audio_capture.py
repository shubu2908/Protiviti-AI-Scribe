import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundcard as sc
import soundfile as sf

from config import (
    AUDIO_CHANNELS,
    AUDIO_CHUNK_DURATION,
    AUDIO_SAMPLE_RATE,
    OUTPUT_DIR,
    SILENCE_RMS_THRESHOLD,
)

logger = logging.getLogger(__name__)


class AudioCapture:
    def __init__(self, output_dir: Optional[str] = None) -> None:
        self._output_dir = Path(output_dir) if output_dir else Path(OUTPUT_DIR)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._callback: Optional[Callable[[str, int], None]] = None
        self._saved_files: list[str] = []
        self._chunk_index = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_loopback(self) -> sc.core.Microphone:
        """Return the first available WASAPI loopback device."""
        # Preferred: default speaker's loopback via include_loopback
        try:
            speaker = sc.default_speaker()
            loopback = sc.get_microphone(speaker.id, include_loopback=True)
            logger.info("Using default speaker loopback: %s", speaker.name)
            return loopback
        except Exception as exc:
            logger.warning("Default speaker loopback failed (%s), scanning all loopback mics…", exc)

        # Fallback: any loopback microphone in the system
        try:
            loopbacks = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
            if loopbacks:
                logger.info("Using loopback device: %s", loopbacks[0].name)
                return loopbacks[0]
        except Exception as exc:
            logger.warning("Loopback mic scan failed (%s), trying all speakers…", exc)

        # Last resort: iterate speakers and try get_microphone by id
        for speaker in sc.all_speakers():
            try:
                loopback = sc.get_microphone(speaker.id, include_loopback=True)
                logger.info("Using loopback from speaker: %s", speaker.name)
                return loopback
            except Exception:
                continue

        raise RuntimeError(
            "No WASAPI loopback device found.\n"
            "Make sure you are on Windows 10/11 and audio is playing through any output device.\n"
            "Verify that 'soundcard' is installed: pip install soundcard"
        )

    def _is_silent(self, audio_data: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(audio_data ** 2)))
        return rms < SILENCE_RMS_THRESHOLD

    # ------------------------------------------------------------------
    # Recording loop (runs in a daemon thread)
    # ------------------------------------------------------------------

    def _record_loop(self) -> None:
        frames_per_chunk = AUDIO_CHUNK_DURATION * AUDIO_SAMPLE_RATE
        try:
            loopback = self._get_loopback()
        except RuntimeError as exc:
            logger.error("AudioCapture: %s", exc)
            return

        logger.info("AudioCapture: recording started (chunk=%ds, rate=%d Hz)", AUDIO_CHUNK_DURATION, AUDIO_SAMPLE_RATE)

        with loopback.recorder(samplerate=AUDIO_SAMPLE_RATE, channels=AUDIO_CHANNELS) as recorder:
            while not self._stop_event.is_set():
                try:
                    data: np.ndarray = recorder.record(numframes=frames_per_chunk)
                except Exception as exc:
                    logger.error("AudioCapture record error: %s", exc)
                    break

                # Flatten to mono if soundcard returned stereo
                if data.ndim > 1:
                    data = data.mean(axis=1)

                idx = self._chunk_index
                self._chunk_index += 1

                if self._is_silent(data):
                    logger.debug("Chunk %04d is silent — skipping", idx)
                    continue

                timestamp = datetime.now().strftime("%H%M%S")
                filename = str(self._output_dir / f"chunk_{idx:04d}_{timestamp}.wav")
                try:
                    sf.write(filename, data, AUDIO_SAMPLE_RATE, subtype="PCM_16")
                    logger.info("Saved audio chunk: %s", filename)
                    self._saved_files.append(filename)
                    if self._callback:
                        self._callback(filename, idx)
                except Exception as exc:
                    logger.error("Failed to save chunk %04d: %s", idx, exc)

        logger.info("AudioCapture: recording thread exited")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, callback: Optional[Callable[[str, int], None]] = None) -> None:
        """Start recording in a background daemon thread."""
        self._callback = callback
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._record_loop, daemon=True, name="AudioCapture")
        self._thread.start()
        logger.info("AudioCapture thread started")

    def stop(self) -> list[str]:
        """Signal the recording thread to stop and return paths to all saved WAV files."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=AUDIO_CHUNK_DURATION + 5)
        logger.info("AudioCapture stopped. %d chunks saved.", len(self._saved_files))
        return list(self._saved_files)
