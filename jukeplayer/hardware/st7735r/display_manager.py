from jukeplayer.core.state_constants import *
from jukeplayer.core.logger import log
import asyncio
import time
from jukeplayer.nanogui.core.writer import CWriter
from jukeplayer.nanogui.core.nanogui import refresh
from jukeplayer.nanogui.widgets.label import Label, ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT
from jukeplayer.nanogui.fonts import geistmonobold10, geistmonobold16, geistmonobold18, geistmonobold14, material_subset
import jukeplayer.hardware.st7735r.st7735r as st7735r


class DisplayManager:
    """ST7735R display manager aligned to the OLED manager state/update contract."""

    def __init__(
        self,
        spi_ctl,
        app_state=None,
        cs=8,
        dc=3,
        rst=46,
        backlight_pin=21,
        width=160,
        height=128,
        color_invert=False,
        backlight_idle_s=0,
        **kwargs,
    ):
        from machine import Pin

        self.width = width
        self.height = height

        self.app_state = app_state
        self.spi_ctl = spi_ctl
        self.display = st7735r.ST7735R(
            spi=spi_ctl.spi,
            cs=self._as_pin(cs, Pin),
            dc=self._as_pin(dc, Pin),
            rst=self._as_pin(rst, Pin),
            width=self.width,
            height=self.height,
            color_invert=color_invert,
        )
        self.backlight = Pin(backlight_pin, Pin.OUT, value=0)
        # Backlight idle management (the ili9488 concepts, 2026-09-21): the
        # show() gate skips the panel DMA while dark; wake() lights and
        # catches up. The ST7735R push is a single ~36ms blocking transfer —
        # no split support, so valid_refresh_splits is empty (the config page
        # hides the split field for this device).
        self._backlight_idle_s = backlight_idle_s
        self._backlight_on = True
        self._playing = False
        self._last_interaction = time.ticks_ms()
        self._idle_task = None
        self.valid_refresh_splits = []
        black = self.display.rgb(0, 0, 0)
        white = self.display.rgb(255, 255, 255)
        cyan = self.display.rgb(0, 200, 200)
        yellow = self.display.rgb(255, 220, 0)

        self.writers = (
            CWriter(self.display, material_subset, fgcolor=yellow, bgcolor=black, verbose=False),
            CWriter(self.display, geistmonobold14, fgcolor=white, bgcolor=black, verbose=False),
            CWriter(self.display, geistmonobold16, fgcolor=white, bgcolor=black, verbose=False),
            CWriter(self.display, geistmonobold10, fgcolor=yellow, bgcolor=black, verbose=False),
        )

        self.current_layout = "status"
        self.current_screen = StatusScreen(self.display, self.writers)
        initial_state = self.app_state.data if self.app_state else {}
        self.current_screen.update(initial_state)
        self.current_screen.draw_static()
        self._show_current()

        self.is_running = False
        self.timer_task = None

    @staticmethod
    def _as_pin(pin_or_num, pin_type):
        if hasattr(pin_or_num, "value"):
            return pin_or_num
        return pin_type(int(pin_or_num), pin_type.OUT)

    def show_message(self, message, duration=5):
        if self.timer_task:
            try:
                self.timer_task.cancel()
            except RuntimeError:
                pass
            self.timer_task = None

        self.current_layout = "message"
        self.current_screen.show_message(str(message), header="Message")
        self.display.fill(0)
        self.current_screen.draw_static()
        self._show_current()

        if duration is not None:
            self.timer_task = asyncio.create_task(
                self._layout_timer_loop(duration, "status")
            )
    def toggle_backlight(self):
        self._set_backlight(not self._backlight_on)
        if self._backlight_on:
            self._last_interaction = time.ticks_ms()
            self._show_current()

    def _set_backlight(self, on):
        """Drive the backlight (0 = on for this module) and track the state."""
        self.backlight.value(0 if on else 1)
        self._backlight_on = on

    def _show_current(self):
        """Gate: skip the panel DMA while the backlight is off (the corruption/
        LED/burn-in win). The framebuffer keeps updating; wake() pushes."""
        if not self._backlight_on:
            log.debug("[TFT] refresh skipped — backlight off")
            return
        self.display.show()

    def wake(self):
        """Any user interaction: light the panel if dark (with one full push to
        catch up skipped transfers) and restart the idle timer."""
        self._last_interaction = time.ticks_ms()
        if not self._backlight_on:
            self._set_backlight(True)
            log.info("[TFT] wake — interaction")
            self._show_current()
        self._last_interaction = time.ticks_ms()

    def _auto_backlight_tick(self):
        """Idle check: darken when not playing and untouched for the window."""
        if not self._backlight_idle_s or not self._backlight_on or self._playing:
            log.debug(f"[TFT] idle tick skip: idle_s={self._backlight_idle_s} on={self._backlight_on} playing={self._playing}")
            return
        elapsed = time.ticks_diff(time.ticks_ms(), self._last_interaction)
        if elapsed < self._backlight_idle_s * 1000:
            log.debug(f"[TFT] idle tick: {elapsed // 1000}s / {self._backlight_idle_s}s")
            return
        self._set_backlight(False)
        log.info(f"[TFT] backlight auto-off (idle {self._backlight_idle_s}s)")

    async def _idle_loop(self):
        while True:
            try:
                self._auto_backlight_tick()
            except Exception as e:
                log.error(f"[TFT] idle tick error: {e}")
            await asyncio.sleep(10)

    def switch_layout(self, layout_name, duration=None, fallback_layout=None):
        if self.timer_task:
            try:
                self.timer_task.cancel()
            except RuntimeError:
                pass
            self.timer_task = None

        if layout_name == "status":
            self.current_layout = layout_name
            self.current_screen.clear_message()
            self.display.fill(0)
            current_state = self.app_state.data if self.app_state else {}
            self.current_screen.update(current_state)
            self.current_screen.draw_static()
            self._show_current()

            if duration is not None and fallback_layout == "message":
                self.timer_task = asyncio.create_task(
                    self._layout_timer_loop(duration, fallback_layout)
                )
        elif layout_name == "message":
            self.current_layout = layout_name
            self.display.fill(0)
            self.current_screen.draw_static()
            self._show_current()

    async def _layout_timer_loop(self, duration, fallback_layout):
        try:
            await asyncio.sleep(duration)
            self.timer_task = None
            self.switch_layout(fallback_layout)
        except asyncio.CancelledError:
            pass
        finally:
            if self.timer_task == asyncio.current_task():
                self.timer_task = None

    def get_current_layout(self):
        return self.current_layout

    def update(self, state):
        # Start the idle task from the loop context (before the early return —
        # non-visual deltas also count as "the loop is running").
        self._ensure_idle_task()
        for key in state:
            if key not in NON_VISUAL_KEYS:
                break
        else:
            return  # delta contains only non-visual keys — no repaint needed
        self._ensure_idle_task()
        if PLAYER_STATUS in state:
            playing = state[PLAYER_STATUS] == "PLAY"
            if playing and not self._backlight_on:
                self.wake()  # remote play start (web UI) lights the panel
            self._playing = playing
        self.current_screen.update(state)
        self._show_current()

    def start(self):
        if not self.is_running:
            self.is_running = True
            # NOTE: the idle task is NOT started here — create_task from the
            # sync __init__ context doesn't schedule. It starts from update().
            if hasattr(self.current_screen, "set_initial_boot_state"):
                self.current_screen.set_initial_boot_state()
                self._show_current()

    async def start_idle_task(self):
        """Start the idle task. MUST be called from the loop context (the
        app's run() method) — create_task from the sync __init__ context
        doesn't schedule (proved empirically with the LED blink)."""
        if self._backlight_idle_s > 0 and self._idle_task is None:
            self._idle_task = asyncio.create_task(self._idle_loop())
            log.debug("[TFT] idle task started from loop context")

    def _ensure_idle_task(self):
        """Legacy sync path — kept for backward compatibility but unreliable
        (the same scheduling issue). Use start_idle_task() from the app."""
        """Start the idle task from the loop context (called from update(),
        which runs inside the running event loop)."""
        if self._backlight_idle_s > 0 and self._idle_task is None:
            self._idle_task = asyncio.create_task(self._idle_loop())

    def stop(self):
        self.is_running = False
        if self.timer_task:
            self.timer_task.cancel()
            self.timer_task = None
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None


class StatusScreen:
    def __init__(self, display, writers):
        self.display = display
        self.writer_symbols = writers[0]
        self.writer_small = writers[1]
        self.writer_large = writers[2]
        self.writer_mini = writers[3]

        safe_width = max(1, self.display.width - 1)
        cyan = self.display.rgb(0, 200, 200)
        top_row = 2
        row_artist = 33
        row_album = 60
        row_title = 84
        row_title_2 = 101
        row_message = 117

        self.label_volume = Label(self.writer_small, top_row, 0, 30, align=ALIGN_LEFT, bdcolor=False)
        self.label_status = Label(self.writer_symbols, top_row, max(0, safe_width - 80), 80, align=ALIGN_RIGHT, bdcolor=False)
        self.label_artist = Label(self.writer_large, row_artist, 0, safe_width, align=ALIGN_CENTER, bdcolor=False)
        self.label_album = Label(self.writer_small, row_album, 0, safe_width, align=ALIGN_CENTER, bdcolor=False)
        self.label_title1 = Label(self.writer_small, row_title, 0, safe_width, align=ALIGN_CENTER, bdcolor=False, fgcolor=cyan)
        self.label_title2 = Label(self.writer_small, row_title_2, 0, safe_width, align=ALIGN_CENTER, bdcolor=False, fgcolor=cyan)
        self.label_message = Label(self.writer_mini, row_message, 0, safe_width, align=ALIGN_CENTER, bdcolor=False)
        self.message_active = False

        # Cached icon state for the combined status label (the same pattern
        # as the ili9488: net + player + repeat + mute, left to right).
        self._net_icon = ""
        self._player_icon = ""
        self._repeat_icon = ""
        self._mute_icon = ""

        refresh(self.display, clear=True)

    def draw_static(self):
        self.label_status.show()
        self.label_volume.show()
        self.label_artist.show()
        self.label_album.show()
        self.label_title1.show()
        self.label_title2.show()
        # Cached icon state for the combined status label (the same pattern
        # as the ili9488: net + player + repeat + mute, left to right).
        self._net_icon = ""
        self._player_icon = ""
        self._repeat_icon = ""
        self._mute_icon = ""
        self.label_title1.show()
        self.label_title2.show()
        self.label_message.show()

    def set_initial_boot_state(self):
        self._set_title("Jukeplayer")
        self._set_artist("Booting...")
        self._set_net_status("WS:CON")
        self._set_player_status("BOOT")

    def update(self, state={}):
        if self.message_active:
            return

        if VOLUME in state:
            self._set_volume(state[VOLUME])
        if ARTIST in state:
            self._set_artist(state[ARTIST])
        if ALBUM in state:
            self._set_album(state[ALBUM])
        if TITLE in state:
            self._set_title(state[TITLE])
        if PLAYER_STATUS in state:
            self._set_player_status(state[PLAYER_STATUS])
        if NETWORK_STATUS in state:
            self._set_net_status(state[NETWORK_STATUS])
        if REPEAT_STATUS in state:
            self._set_repeat_status(state[REPEAT_STATUS])
        if MUTED in state:
            self._set_mute_status(state[MUTED])


    def _set_net_status(self, status):
        status = status.upper()
        if status == "WS:OK":
            self._net_icon = "\ue308"
        elif status == "WS:CON":
            self._net_icon = "\ue63e"
        else:
            self._net_icon = "\ue648"
        self._update_status_label()

    def _set_player_status(self, status):
        status = status.upper()
        if status == "PLAY":
            self._player_icon = "\ue037"
        elif status == "STOP" or status == "BOOT":
            self._player_icon = "\ue047"
        elif status == "PAUSE":
            self._player_icon = "\ue034"
        else:
            self._player_icon = ""
        self._update_status_label()

    def _set_repeat_status(self, repeat):
        self._repeat_icon = "\ue040" if repeat else ""
        self._update_status_label()

    def _set_mute_status(self, muted):
        self._mute_icon = "\ue308" if muted else ""
        self._update_status_label()

    def _update_status_label(self):
        self.label_status.value(self._net_icon + self._player_icon + self._repeat_icon + self._mute_icon)

    def _set_player_status(self, status):
        status = status.upper()
        if status == "PLAY":
            self._player_icon = "\ue037"
        elif status == "STOP" or status == "BOOT":
            self._player_icon = "\ue047"
        elif status == "PAUSE":
            self._player_icon = "\ue034"
        else:
            self._player_icon = ""
        self._update_status_label()

    def _set_volume(self, volume):
        self.label_volume.value(f"{volume}%")

    def _set_artist(self, artist):
        self.label_artist.value(str(artist or ""))

    def _set_album(self, album):
        self.label_album.value(str(album or ""))

    def _set_title(self, title):
        title = str(title or "")
        line1, line2 = self.split_string_to_width(title)
        self.label_title1.value(line1)
        self.label_title2.value(line2)

    def split_string_to_width(self, text):
        text = str(text or "")
        if self.writer_small.stringlen(text) <= self.display.width:
            return text, ""

        words = text.split()
        best_split = 0
        best_diff = None

        # Try every possible word boundary and pick the split where both lines
        # fit on screen and their pixel lengths are closest to equal.
        for i in range(1, len(words)):
            line1 = " ".join(words[:i])
            line2 = " ".join(words[i:])
            len1 = self.writer_small.stringlen(line1)
            len2 = self.writer_small.stringlen(line2)

            if len1 <= self.display.width and len2 <= self.display.width:
                diff = abs(len1 - len2)
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best_split = i

        if best_split > 0:
            line1 = " ".join(words[:best_split])
            line2 = " ".join(words[best_split:])
            return line1, line2

        # Fallback: greedy fill line 1 if no balanced split works
        line1 = ""
        for word in words:
            test = line1 + (" " if line1 else "") + word
            if self.writer_small.stringlen(test) <= self.display.width:
                line1 = test
            else:
                break

        if line1:
            line1_words_count = len(line1.split())
            line2 = " ".join(words[line1_words_count:])
        else:
            line1 = words[0] if words else ""
            line2 = " ".join(words[1:]) if len(words) > 1 else ""

        return line1, line2  

    def show_message(self, text, header="Message"):
        """Render a notice into the dedicated bottom message field.
        (header kept for API parity with the other managers; unused here)"""
        self.message_active = True
        self.label_message.value(str(text))

    def clear_message(self):
        self.message_active = False
        self.label_message.value("")

