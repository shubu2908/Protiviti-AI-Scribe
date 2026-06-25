"""
Protiviti AI Scribe — Windows System Tray Application
Right-click the tray icon → Start Scribe → paste URL → done.
No terminal window needed.
"""

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import pystray
from pystray import Menu, MenuItem
from PIL import Image, ImageDraw
import tkinter as tk
from tkinter import messagebox, simpledialog

# All paths relative to this file so the app works from any directory
SCRIPT_DIR = Path(__file__).parent.resolve()
LOG_FILE = SCRIPT_DIR / "scribe.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tray")


# ---------------------------------------------------------------------------
# Icon drawing
# ---------------------------------------------------------------------------

def _draw_icon(recording: bool = False) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Background circle — blue when ready, dark red when recording
    bg = (180, 20, 20) if recording else (0, 48, 135)
    d.ellipse([2, 2, 62, 62], fill=bg)

    # Microphone capsule
    d.ellipse([24, 10, 40, 34], fill="white")
    d.rectangle([24, 22, 40, 40], fill="white")

    # Microphone stand arc
    d.arc([17, 32, 47, 52], start=0, end=180, fill="white", width=3)
    d.line([32, 52, 32, 59], fill="white", width=3)
    d.line([26, 59, 38, 59], fill="white", width=3)

    # Red recording indicator dot
    if recording:
        d.ellipse([46, 4, 62, 20], fill=(255, 80, 80))

    return img


# ---------------------------------------------------------------------------
# Thread-safe Tkinter helpers
# ---------------------------------------------------------------------------

def _run_in_thread(fn):
    """Run fn in a daemon thread (used to open Tkinter dialogs from pystray callbacks)."""
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    return t


def _ask(title: str, prompt: str, initial: str = "") -> str | None:
    """Show a simple text-input dialog and return the value (or None if cancelled)."""
    result: list[str | None] = [None]
    done = threading.Event()

    def _show():
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        val = simpledialog.askstring(title, prompt, initialvalue=initial, parent=root)
        result[0] = val
        root.destroy()
        done.set()

    _run_in_thread(_show)
    done.wait(timeout=300)
    return result[0]


def _info(title: str, msg: str) -> None:
    def _show():
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, msg, parent=root)
        root.destroy()
    _run_in_thread(_show)


def _error(title: str, msg: str) -> None:
    def _show():
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(title, msg, parent=root)
        root.destroy()
    _run_in_thread(_show)


def _yesno(title: str, msg: str) -> bool:
    result = [False]
    done = threading.Event()

    def _show():
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        result[0] = messagebox.askyesno(title, msg, parent=root)
        root.destroy()
        done.set()

    _run_in_thread(_show)
    done.wait(timeout=60)
    return result[0]


# ---------------------------------------------------------------------------
# Tray application
# ---------------------------------------------------------------------------

class ScribeTrayApp:
    def __init__(self) -> None:
        self._bot_process: subprocess.Popen | None = None
        self._icon: pystray.Icon | None = None
        self._recording = False

    # ------------------------------------------------------------------ icon

    def _refresh_icon(self) -> None:
        if self._icon:
            self._icon.icon = _draw_icon(self._recording)
            status = "Recording…  Right-click to stop" if self._recording else "Ready — right-click to start"
            self._icon.title = f"Protiviti AI Scribe — {status}"

    # ------------------------------------------------------------------ env check

    def _env_ok(self) -> bool:
        env_path = SCRIPT_DIR / ".env"
        if not env_path.exists():
            _error(
                "Setup Required",
                f"'.env' file not found.\n\n"
                f"Run install.bat first, then open .env and add your GEMINI_API_KEY.\n\n"
                f"Folder: {SCRIPT_DIR}",
            )
            return False
        content = env_path.read_text(encoding="utf-8")
        if "GEMINI_API_KEY=your-gemini" in content or "GEMINI_API_KEY=" not in content:
            _error(
                "API Key Missing",
                "GEMINI_API_KEY is not set in .env\n\n"
                "Get your free key at:\nhttps://aistudio.google.com/app/apikey\n\n"
                "Then open .env (Configure menu) and paste it in.",
            )
            return False
        return True

    # ------------------------------------------------------------------ actions

    def on_start(self, icon, item) -> None:
        if self._recording:
            _error("Already Recording", "Scribe is already in a meeting.\nStop it first.")
            return
        if not self._env_ok():
            return

        url = _ask("Protiviti AI Scribe", "Paste the Teams meeting URL:")
        if not url or not url.strip():
            return

        title = _ask("Protiviti AI Scribe", "Meeting title (shown in MoM):", initial="Teams Meeting")
        if title is None:
            return
        title = title.strip() or "Teams Meeting"

        _run_in_thread(lambda: self._run_bot(url.strip(), title))

    def _run_bot(self, url: str, title: str) -> None:
        self._recording = True
        self._refresh_icon()
        logger.info("Starting bot — title: %s", title)

        log_handle = open(LOG_FILE, "a", encoding="utf-8")
        log_handle.write(f"\n{'='*60}\nMeeting: {title}\nURL: {url}\n{'='*60}\n")
        log_handle.flush()
        try:
            self._bot_process = subprocess.Popen(
                [sys.executable, str(SCRIPT_DIR / "main.py"),
                 "--url", url, "--title", title],
                cwd=str(SCRIPT_DIR),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            exit_code = self._bot_process.wait()
        except Exception as exc:
            logger.error("Bot launch error: %s", exc)
            exit_code = -1
        finally:
            log_handle.close()
            self._recording = False
            self._bot_process = None
            self._refresh_icon()

        if exit_code == 0:
            out_dir = SCRIPT_DIR / "meeting_output"
            try:
                self._icon.notify(
                    f"'{title}' recording complete. MoM saved & emailed.",
                    "Protiviti AI Scribe",
                )
            except Exception:
                pass
            _info(
                "Session Complete ✓",
                f"Recording finished for:\n{title}\n\n"
                f"Files saved to:\n{out_dir}\n\n"
                f"(MoM emailed if SMTP is configured in .env)",
            )
        else:
            _error(
                "Recording Error",
                f"Bot exited with code {exit_code}.\n\nCheck scribe.log for details:\n{LOG_FILE}",
            )

    def on_stop(self, icon, item) -> None:
        if self._bot_process and self._recording:
            if _yesno("Stop Recording?", "Stop the current recording?\nOutput files will still be generated."):
                self._bot_process.terminate()
        else:
            _error("Not Recording", "No active recording session.")

    def on_open_output(self, icon, item) -> None:
        out = SCRIPT_DIR / "meeting_output"
        out.mkdir(exist_ok=True)
        os.startfile(str(out))

    def on_view_log(self, icon, item) -> None:
        if LOG_FILE.exists():
            os.startfile(str(LOG_FILE))
        else:
            _info("No Log Yet", "No log file found. Start a recording first.")

    def on_configure(self, icon, item) -> None:
        env = SCRIPT_DIR / ".env"
        if not env.exists():
            import shutil
            ex = SCRIPT_DIR / ".env.example"
            if ex.exists():
                shutil.copy(ex, env)
        os.startfile(str(env))

    def on_quit(self, icon, item) -> None:
        if self._recording:
            if not _yesno("Quit?", "Recording is in progress.\nStop recording and quit?"):
                return
            if self._bot_process:
                self._bot_process.terminate()
        icon.stop()

    # ------------------------------------------------------------------ run

    def run(self) -> None:
        menu = Menu(
            MenuItem("▶  Start Scribe", self.on_start, default=True),
            MenuItem("■  Stop Recording", self.on_stop),
            Menu.SEPARATOR,
            MenuItem("📁  Open Output Folder", self.on_open_output),
            MenuItem("📋  View Log", self.on_view_log),
            MenuItem("⚙  Configure (.env)", self.on_configure),
            Menu.SEPARATOR,
            MenuItem("Quit", self.on_quit),
        )
        self._icon = pystray.Icon(
            name="Protiviti AI Scribe",
            icon=_draw_icon(recording=False),
            title="Protiviti AI Scribe — Ready",
            menu=menu,
        )
        logger.info("Tray app started")
        self._icon.run()


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ScribeTrayApp().run()
