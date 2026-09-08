from jukeplayer.core.state_constants import *
import asyncio
from jukeplayer.nanogui.core.writer import CWriter
from jukeplayer.nanogui.core.nanogui import refresh
from jukeplayer.nanogui.widgets.label import Label, ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT
from jukeplayer.nanogui.fonts import geistmonobold10, geistmonobold18, geistmonobold14, material_subset
import jukeplayer.hardware.st7735r.st7735r as st7735r


class DisplayManager:
    """ST7735R display manager aligned to the OLED manager state/update contract."""

    def __init__(
        self,
        spi,
        app_state=None,
        cs=8,
        dc=3,
        rst=46,
        backlight_pin=21,
        width=160,
        height=128,
        init_spi=None,
        color_invert=False,
        **kwargs,
    ):
        from machine import Pin

        self.width = width
        self.height = height

        self.app_state = app_state
        self.display = st7735r.ST7735R(
            spi=spi,
            cs=self._as_pin(cs, Pin),
            dc=self._as_pin(dc, Pin),
            rst=self._as_pin(rst, Pin),
            width=self.width,
            height=self.height,
            init_spi=init_spi or False,
            color_invert=color_invert,
        )
        self.backlight = Pin(backlight_pin, Pin.OUT, value=0)
        black = self.display.rgb(0, 0, 0)
        white = self.display.rgb(255, 255, 255)
        cyan = self.display.rgb(0, 200, 200)
        yellow = self.display.rgb(255, 220, 0)

        self.writers = (
            CWriter(self.display, material_subset, fgcolor=yellow, bgcolor=black, verbose=False),
            CWriter(self.display, geistmonobold14, fgcolor=white, bgcolor=black, verbose=False),
            CWriter(self.display, geistmonobold18, fgcolor=white, bgcolor=black, verbose=False),
            CWriter(self.display, geistmonobold10, fgcolor=yellow, bgcolor=black, verbose=False),
        )

        self.current_layout = "status"
        self.current_screen = StatusScreen(self.display, self.writers)
        initial_state = self.app_state.data if self.app_state else {}
        self.current_screen.update(initial_state)
        self.current_screen.draw_static()
        self.display.show()

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
        self.display.show()

        if duration is not None:
            self.timer_task = asyncio.create_task(
                self._layout_timer_loop(duration, "status")
            )
    def toggle_backlight(self):
        self.backlight.value(0 if self.backlight.value() else 1)

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
            self.display.show()

            if duration is not None and fallback_layout == "message":
                self.timer_task = asyncio.create_task(
                    self._layout_timer_loop(duration, fallback_layout)
                )
        elif layout_name == "message":
            self.current_layout = layout_name
            self.display.fill(0)
            self.current_screen.draw_static()
            self.display.show()

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
        for key in state:
            if key not in NON_VISUAL_KEYS:
                break
        else:
            return  # delta contains only non-visual keys — no repaint needed
        self.current_screen.update(state)
        self.display.show()

    def start(self):
        if not self.is_running:
            self.is_running = True
            if hasattr(self.current_screen, "set_initial_boot_state"):
                self.current_screen.set_initial_boot_state()
                self.display.show()

    def stop(self):
        self.is_running = False
        if self.timer_task:
            self.timer_task.cancel()
            self.timer_task = None


class StatusScreen:
    def __init__(self, display, writers):
        self.display = display
        self.writer_symbols = writers[0]
        self.writer_small = writers[1]
        self.writer_large = writers[2]
        self.writer_mini = writers[3]

        safe_width = max(1, self.display.width - 1)
        top_row = 2
        row_artist = 44
        row_title = 70
        row_title_2 = 88
        row_message = 108

        self.label_net = Label(self.writer_symbols, top_row, 0, 64, align=ALIGN_LEFT, bdcolor=False)
        self.label_player_status = Label(self.writer_symbols, top_row, max(0, safe_width - 63), 63, align=ALIGN_RIGHT, bdcolor=False)
        self.label_volume = Label(self.writer_small, top_row, 65, 20, align=ALIGN_CENTER, bdcolor=False)
        self.label_artist = Label(self.writer_small, row_artist, 0, safe_width, align=ALIGN_CENTER, bdcolor=False)
        self.label_title1 = Label(self.writer_small, row_title, 0, safe_width, align=ALIGN_CENTER, bdcolor=False)
        self.label_title2 = Label(self.writer_small, row_title_2, 0, safe_width, align=ALIGN_CENTER, bdcolor=False)
        self.label_message = Label(self.writer_mini, row_message, 0, safe_width, align=ALIGN_CENTER, bdcolor=False)
        self.message_active = False

        refresh(self.display, clear=True)

    def draw_static(self):
        self.label_net.show()
        self.label_player_status.show()
        self.label_volume.show()
        self.label_artist.show()
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
        if TITLE in state:
            self._set_title(state[TITLE])
        if PLAYER_STATUS in state:
            self._set_player_status(state[PLAYER_STATUS])
        if NETWORK_STATUS in state:
            self._set_net_status(state[NETWORK_STATUS])

    def _set_net_status(self, status):
        self.label_net.value(str(status).upper())

    def _set_net_status(self, status):
        status = status.upper()
        if status == "WS:OK":
            self.label_net.value("\ue308")
        elif status == "WS:CON":
            self.label_net.value("\ue63e")
        else:
            self.label_net.value("\ue648")

    def _set_player_status(self, status):
        status = status.upper()
        if status == "PLAY":
            self.label_player_status.value("\ue037")
        elif status == "STOP" or status == "BOOT":
            self.label_player_status.value("\ue047")
        elif status == "PAUSE":
            self.label_player_status.value("\ue034")
        else:
            self.label_player_status.value("")   

    def _set_volume(self, volume):
        self.label_volume.value(f"{volume}%")

    def _set_artist(self, artist):
        self.label_artist.value(str(artist or ""))

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

