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
    SILENCE_TIMEOUT_MINUTES,
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
        self._lb_stream = None   # kept so stop() can forcefully close it
        self._mic_stream = None
        self._pa_instance = None
        self._had_speech = False           # True once any non-silent chunk was captured
        self._consecutive_silent_chunks = 0  # used as a meeting-end safety net

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_loopback(self, p: pyaudio.PyAudio) -> dict:
        """Return the loopback device info for the default output device."""
        try:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            raise RuntimeError("WASAPI not available — requires Windows 10/11.")

        default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        logger.info("Default output: %s", default_out["name"])

        for lb in p.get_loopback_device_info_generator():
            if default_out["name"] in lb["name"]:
                logger.info("Loopback device: %s", lb["name"])
                return lb

        loopbacks = list(p.get_loopback_device_info_generator())
        if loopbacks:
            logger.warning("Using fallback loopback: %s", loopbacks[0]["name"])
            return loopbacks[0]

        raise RuntimeError(
            "No WASAPI loopback device found.\n"
            "Ensure Windows audio output is active and pyaudiowpatch is installed."
        )

    def _get_mic(self, p: pyaudio.PyAudio, target_rate: int) -> Optional[dict]:
        """Return the default microphone device info if available."""
        try:
            mic = p.get_default_input_device_info()
            if mic.get("isLoopbackDevice"):
                return None
            logger.info("Microphone: %s", mic["name"])
            return mic
        except Exception as exc:
            logger.warning("No default microphone found: %s", exc)
            return None

    def _is_silent(self, audio: np.ndarray) -> bool:
        return float(np.sqrt(np.mean(audio ** 2))) < SILENCE_RMS_THRESHOLD

    def _save_chunk(self, data: np.ndarray, sample_rate: int, min_seconds: float = 2.0) -> None:
        """Save a numpy chunk to WAV and trigger the callback."""
        if len(data) < sample_rate * min_seconds:
            return
        if self._is_silent(data):
            logger.debug("Chunk %04d is silent — skipping", self._chunk_index)
            self._chunk_index += 1
            self._consecutive_silent_chunks += 1
            return

        self._had_speech = True
        self._consecutive_silent_chunks = 0

        idx = self._chunk_index
        self._chunk_index += 1
        ts = datetime.now().strftime("%H%M%S")
        filename = str(self._output_dir / f"chunk_{idx:04d}_{ts}.wav")
        try:
            sf.write(filename, data, sample_rate, subtype="PCM_16")
            logger.info("Saved audio chunk: %s", filename)
            self._saved_files.append(filename)
            if self._callback:
                self._callback(filename, idx)
        except Exception as exc:
            logger.error("Failed to save chunk %04d: %s", idx, exc)

    # ------------------------------------------------------------------
    # Recording loop — captures loopback + mic, mixes them
    # ------------------------------------------------------------------

    def _record_loop(self) -> None:
        p = pyaudio.PyAudio()
        self._pa_instance = p

        try:
            lb_info = self._get_loopback(p)
        except RuntimeError as exc:
            logger.error("AudioCapture: %s", exc)
            p.terminate()
            return

        sample_rate = int(lb_info["defaultSampleRate"])
        lb_channels = lb_info["maxInputChannels"]
        frames_per_buf = 512
        frames_per_chunk = sample_rate * AUDIO_CHUNK_DURATION

        mic_info = self._get_mic(p, sample_rate)

        logger.info(
            "AudioCapture: loopback=%s | mic=%s | %dHz | chunk=%ds",
            lb_info["name"], mic_info["name"] if mic_info else "none",
            sample_rate, AUDIO_CHUNK_DURATION,
        )

        # Open loopback stream
        try:
            self._lb_stream = p.open(
                format=pyaudio.paInt16,
                channels=lb_channels,
                rate=sample_rate,
                frames_per_buffer=frames_per_buf,
                input=True,
                input_device_index=lb_info["index"],
            )
        except Exception as exc:
            logger.error("Cannot open loopback stream: %s", exc)
            p.terminate()
            return

        # Open microphone stream (best-effort)
        if mic_info:
            try:
                self._mic_stream = p.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=sample_rate,
                    frames_per_buffer=frames_per_buf,
                    input=True,
                    input_device_index=mic_info["index"],
                )
                logger.info("Microphone stream opened at %dHz", sample_rate)
            except Exception as exc:
                logger.warning("Could not open mic stream (loopback only): %s", exc)
                self._mic_stream = None

        accumulated: list[np.ndarray] = []

        try:
            while not self._stop_event.is_set():
                # --- read loopback ---
                try:
                    lb_raw = self._lb_stream.read(frames_per_buf, exception_on_overflow=False)
                except Exception:
                    break  # stream was closed by stop()

                lb_arr = np.frombuffer(lb_raw, dtype=np.int16).astype(np.float32) / 32768.0
                if lb_channels > 1:
                    lb_arr = lb_arr.reshape(-1, lb_channels).mean(axis=1)

                # --- read mic and mix ---
                if self._mic_stream:
                    try:
                        mic_raw = self._mic_stream.read(frames_per_buf, exception_on_overflow=False)
                        mic_arr = np.frombuffer(mic_raw, dtype=np.int16).astype(np.float32) / 32768.0
                        mixed = np.clip(lb_arr + mic_arr, -1.0, 1.0)
                    except Exception:
                        mixed = lb_arr
                else:
                    mixed = lb_arr

                accumulated.append(mixed)

                # When we have a full chunk, save it
                total = sum(len(a) for a in accumulated)
                if total >= frames_per_chunk:
                    chunk_data = np.concatenate(accumulated)
                    self._save_chunk(chunk_data[:frames_per_chunk], sample_rate)
                    # Keep any overflow for the next chunk
                    leftover = chunk_data[frames_per_chunk:]
                    accumulated = [leftover] if len(leftover) > 0 else []

        finally:
            # Save any remaining audio (partial last chunk ≥ 5s)
            if accumulated:
                leftover = np.concatenate(accumulated)
                if len(leftover) >= sample_rate * 5:
                    logger.info("Saving partial final chunk (%ds)", len(leftover) // sample_rate)
                    self._save_chunk(leftover, sample_rate, min_seconds=5.0)

            self._close_streams()
            p.terminate()
            self._pa_instance = None
            logger.info("AudioCapture: recording thread exited")

    def _close_streams(self) -> None:
        for stream in (self._lb_stream, self._mic_stream):
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        self._lb_stream = None
        self._mic_stream = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, callback: Optional[Callable[[str, int], None]] = None) -> None:
        self._callback = callback
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._record_loop, daemon=True, name="AudioCapture")
        self._thread.start()
        logger.info("AudioCapture thread started")

    def stop(self) -> list[str]:
        """Signal stop, immediately close streams (unblocks any pending read), join thread."""
        self._stop_event.set()
        self._close_streams()  # force-unblock stream.read() instantly
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("AudioCapture stopped. %d chunks saved.", len(self._saved_files))
        return list(self._saved_files)

    def likely_meeting_ended(self) -> bool:
        """Safety net: True if speech was heard earlier but it's now been silent
        for SILENCE_TIMEOUT_MINUTES straight — used when browser-based end
        detection might fail (e.g. a Teams UI change on another machine)."""
        silence_minutes = self._consecutive_silent_chunks * AUDIO_CHUNK_DURATION / 60
        return self._had_speech and silence_minutes >= SILENCE_TIMEOUT_MINUTES
