import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile as sf

from config import (
    AUDIO_CHUNK_DURATION,
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

    def _get_loopback(self) -> tuple:
        """Return (PyAudio instance, loopback device info dict)."""
        p = pyaudio.PyAudio()
        try:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            p.terminate()
            raise RuntimeError(
                "WASAPI not available. This tool requires Windows 10/11 with WASAPI audio."
            )

        # Find the default output device's loopback counterpart
        try:
            default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
            logger.info("Default output device: %s", default_out["name"])
        except Exception as exc:
            p.terminate()
            raise RuntimeError(f"Could not get default output device: {exc}")

        for lb in p.get_loopback_device_info_generator():
            if default_out["name"] in lb["name"]:
                logger.info("Loopback device: %s", lb["name"])
                return p, lb

        # Fallback: return the first available loopback
        loopbacks = list(p.get_loopback_device_info_generator())
        if loopbacks:
            logger.warning(
                "Default speaker loopback not found; using first available: %s",
                loopbacks[0]["name"],
            )
            return p, loopbacks[0]

        p.terminate()
        raise RuntimeError(
            "No WASAPI loopback device found.\n"
            "Make sure you are on Windows 10/11 and audio output is active.\n"
            "Check: Start → Sound Settings → Output device is set correctly."
        )

    def _is_silent(self, audio_data: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(audio_data ** 2)))
        return rms < SILENCE_RMS_THRESHOLD

    # ------------------------------------------------------------------
    # Recording loop (runs in a daemon thread)
    # ------------------------------------------------------------------

    def _record_loop(self) -> None:
        try:
            p, lb = self._get_loopback()
        except RuntimeError as exc:
            logger.error("AudioCapture: %s", exc)
            return

        sample_rate = int(lb["defaultSampleRate"])
        channels = lb["maxInputChannels"]
        frames_per_buffer = 512
        frames_per_chunk = sample_rate * AUDIO_CHUNK_DURATION

        logger.info(
            "AudioCapture started: %s | %d Hz | %d ch | chunk=%ds",
            lb["name"], sample_rate, channels, AUDIO_CHUNK_DURATION,
        )

        stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            frames_per_buffer=frames_per_buffer,
            input=True,
            input_device_index=lb["index"],
        )

        try:
            while not self._stop_event.is_set():
                raw_frames: list[bytes] = []
                frames_recorded = 0
                reads_needed = frames_per_chunk // frames_per_buffer

                for _ in range(reads_needed):
                    if self._stop_event.is_set():
                        break
                    try:
                        data = stream.read(frames_per_buffer, exception_on_overflow=False)
                        raw_frames.append(data)
                        frames_recorded += frames_per_buffer
                    except Exception as exc:
                        logger.error("Stream read error: %s", exc)
                        break

                if not raw_frames:
                    break

                # Convert int16 bytes → float32 numpy array
                pcm = np.frombuffer(b"".join(raw_frames), dtype=np.int16).astype(np.float32)
                pcm /= 32768.0

                # Mix stereo → mono
                if channels > 1:
                    pcm = pcm.reshape(-1, channels).mean(axis=1)

                idx = self._chunk_index
                self._chunk_index += 1

                if self._is_silent(pcm):
                    logger.debug("Chunk %04d is silent — skipping", idx)
                    continue

                timestamp = datetime.now().strftime("%H%M%S")
                filename = str(self._output_dir / f"chunk_{idx:04d}_{timestamp}.wav")
                try:
                    sf.write(filename, pcm, sample_rate, subtype="PCM_16")
                    logger.info("Saved audio chunk: %s", filename)
                    self._saved_files.append(filename)
                    if self._callback:
                        self._callback(filename, idx)
                except Exception as exc:
                    logger.error("Failed to save chunk %04d: %s", idx, exc)
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
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
