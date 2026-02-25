"""
Headless-browser screenshot helper for the Texas Hold'em Arena.

Uses Playwright (Chromium) to capture the live game UI after each action
during spectator mode, producing a sequence of PNG files suitable for
assembling into a demo video.

Installation (one-time):
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Optional


def _safe_label(s: str, max_len: int = 28) -> str:
    """Lowercase, replace non-alphanumeric runs with hyphens, trim."""
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:max_len]


class Screenshotter:
    """
    Opens a headless Chromium window at *url*, then captures screenshots
    on demand via :meth:`capture`.

    Parameters
    ----------
    out_dir : Path
        Directory where PNG files are written.
    url : str
        URL of the live game page (e.g. ``http://127.0.0.1:8000/?game_id=...``).
    viewport : tuple[int, int]
        Browser viewport (width, height) in pixels.
    """

    def __init__(
        self,
        out_dir: Path,
        url: str,
        viewport: tuple[int, int] = (1440, 900),
    ) -> None:
        self.out_dir = out_dir
        self.url = url
        self.viewport = viewport
        self._count = 0
        self._page = None
        self._browser = None
        self._pw = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """
        Launch the headless browser and navigate to the game URL.

        Returns True on success, False if Playwright is not installed or
        the browser fails to start.
        """
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            print(
                "[screenshots] playwright not installed.\n"
                "  Run:  pip install playwright && playwright install chromium"
            )
            return False

        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            ctx = self._browser.new_context(
                viewport={"width": self.viewport[0], "height": self.viewport[1]},
            )
            self._page = ctx.new_page()
            # Use "load" instead of "networkidle": the frontend polls the
            # server every 2 s via setInterval, so the network is *never*
            # idle and "networkidle" would always time out.
            self._page.goto(self.url, wait_until="load", timeout=30_000)
            # Wait for the game UI to actually render (player divs appear
            # after the first successful poll populates the #players container).
            self._page.wait_for_selector(
                "#players .player", state="attached", timeout=30_000,
            )
            self.out_dir.mkdir(parents=True, exist_ok=True)
            print(f"[screenshots] Headless browser ready → {self.out_dir}")
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[screenshots] Failed to start browser: {exc}")
            self._cleanup()
            return False

    def stop(self) -> None:
        """Close the browser, then render the captured frames into an MP4."""
        self._cleanup()
        self.render_video()

    def render_video(self, fps: float = 1.0) -> Optional[Path]:
        """
        Combine all PNG frames in *out_dir* into an MP4 using ffmpeg.

        The video is written to the *parent* of out_dir (i.e. the game's
        data folder) as ``game.mp4``.  Each frame is shown for ``1/fps``
        seconds; the default of 1 fps gives viewers a full second per action.

        Requires ffmpeg to be installed (``brew install ffmpeg`` on macOS).
        Returns the path to the MP4, or None if ffmpeg is missing or there
        are no frames to encode.
        """
        frames = sorted(self.out_dir.glob("*.png"))
        if not frames:
            print("[video] No frames to encode.")
            return None

        # Write an ffmpeg concat-demuxer list so filenames with spaces/globs work.
        list_path = self.out_dir / "_frames.txt"
        with list_path.open("w") as fh:
            for frame in frames:
                duration = 1.0 / fps
                fh.write(f"file '{frame.resolve()}'\n")
                fh.write(f"duration {duration}\n")
            # ffmpeg needs the last entry repeated without a duration to flush it.
            fh.write(f"file '{frames[-1].resolve()}'\n")

        out_mp4 = self.out_dir.parent / "game.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # H.264 requires even dims
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(out_mp4),
        ]

        print(f"[video] Encoding {len(frames)} frames → {out_mp4.name} ...")
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            list_path.unlink(missing_ok=True)   # clean up temp file
            print(f"[video] Saved: {out_mp4}")
            return out_mp4
        except FileNotFoundError:
            print("[video] ffmpeg not found. Install with:  brew install ffmpeg")
            return None
        except subprocess.CalledProcessError as exc:
            print(f"[video] ffmpeg failed:\n{exc.stderr.decode()}")
            return None

    def _cleanup(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._page = None
        self._browser = None
        self._pw = None

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, label: str, extra_wait: float = 2.5) -> Optional[Path]:
        """
        Wait *extra_wait* seconds (for the frontend to poll and re-render),
        then save a full-viewport PNG.

        Parameters
        ----------
        label : str
            Human-readable tag embedded in the filename (will be sanitized).
        extra_wait : float
            Additional seconds to wait *before* snapping the screenshot.
            The frontend polls the server every 2 s, so values ≥ 2.5 s
            reliably capture the updated game state.

        Returns
        -------
        Path | None
            Path of the saved PNG, or None if the browser is not running.
        """
        if self._page is None:
            return None

        time.sleep(extra_wait)

        self._count += 1
        safe = _safe_label(label)
        path = self.out_dir / f"{self._count:04d}_{safe}.png"

        try:
            self._page.screenshot(path=str(path))
            return path
        except Exception as exc:  # noqa: BLE001
            print(f"[screenshots] capture failed: {exc}")
            return None
