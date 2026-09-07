from jukeplayer.core.state_constants import *
import asyncio
import framebuf
from jukeplayer.nanogui.core.writer import Writer
from jukeplayer.nanogui.core.nanogui import refresh
from jukeplayer.nanogui.widgets.label import Label, ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT
from jukeplayer.nanogui.fonts import geistmonobold18, geistmonobold14, material_subset
# from machine import I2C, Pin
import jukeplayer.hardware.oled.ssd1306 as ssd1306


class DisplayManager:
    def __init__(self, i2c, app_state=None):
        self.width = 128
        self.height = 64
        self.display = ssd1306.SSD1306_I2C(self.width, self.height, i2c)
        
        self.app_state = app_state
        
        self.writers = (
            Writer(self.display, material_subset, verbose=False),
            Writer(self.display, geistmonobold14, verbose=False),
            Writer(self.display, geistmonobold18, verbose=False)
        )

        self.current_screen = StatusScreen(self.display, self.writers)
        initial_state = self.app_state.data if self.app_state else {}
        self.current_screen.update(initial_state)
        self.current_screen.draw_static()
        
        self.scroll_task = None
        self.is_running = False

        self.timer_task = None
    
    def show_message(self, message, duration=5):
        """Show a temporary message using the status screen overlay."""
        if self.timer_task:
            try:
                self.timer_task.cancel()
            except RuntimeError:
                pass
            self.timer_task = None

        self.current_screen.show_message(message, header="Message")
        self.display.fill(0)
        self.current_screen.draw_static()
        self.display.show()

        if duration is not None:
            self.timer_task = asyncio.create_task(
                self._layout_timer_loop(duration)
            )

    def revert_to_status(self):
        """Redraw the status layout after a timed message overlay."""
        if self.timer_task:
            try:
                self.timer_task.cancel()
            except RuntimeError:
                pass
            self.timer_task = None

        self.current_screen.clear_message()
        self.display.fill(0)
        current_state = self.app_state.data if self.app_state else {}
        self.current_screen.update(current_state)
        self.current_screen.draw_static()
        self.display.show()

    async def _layout_timer_loop(self, duration):
        """Wait out the message duration, then revert to the status layout."""
        try:
            await asyncio.sleep(duration)
            self.timer_task = None 
            
            self.revert_to_status()
        except asyncio.CancelledError:
            # Task was canceled by an external manual screen switch; exit cleanly
            pass
        finally:
            if self.timer_task == asyncio.current_task():
                self.timer_task = None

    def update(self, state):
        """Pass application state down to the current visible screen and refresh."""
        for key in state:
            if key not in NON_VISUAL_KEYS:
                break
        else:
            return  # delta contains only non-visual keys — no repaint needed
        self.current_screen.update(state)
        self.display.show()

    async def _scroll_loop(self):
        while self.is_running:
            # The current view animates itself and reports if it actually drew anything
            redraw_needed = self.current_screen.update_frame()
            if redraw_needed:
                self.display.show()
                await asyncio.sleep(0.05)  # Fast loop for smooth scrolling (~20fps)
            else:
                await asyncio.sleep(0.25)  # Throttle down when the screen is idling

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.scroll_task = asyncio.create_task(self._scroll_loop())
            
            # Initial boot appearance if starting on status screen
            if hasattr(self.current_screen, 'set_initial_boot_state'):
                self.current_screen.set_initial_boot_state()
                self.display.show()

    def stop(self):
        self.is_running = False
        if self.scroll_task:
            self.scroll_task.cancel()
            self.scroll_task = None

    def toggle_backlight(self):
        """No-op: the I2C SSD1306 has no controllable backlight pin.

        Kept for API parity with the TFT display managers so button
        handlers can call display.toggle_backlight() unconditionally.
        """
        pass

class StatusScreen:
    def __init__(self, display, writers):
        self.display = display
        print("Initializing StatusScreen")
        # Destructure writers for explicit layout mappings
        self.writer_symbols = writers[0]
        self.writer_small = writers[1]
        self.writer_large = writers[2]

        # Layout & Labels
        self.label_wifi_status = Label(self.writer_symbols, 0, 0, 16, align=ALIGN_LEFT, bdcolor=False)
        self.label_ws_status = Label(self.writer_symbols, 0, 16, 16, align=ALIGN_LEFT, bdcolor=False)
        self.label_volume = Label(self.writer_small, 0, 32, 55, align=ALIGN_CENTER, bdcolor=False)
        self.label_player_status = Label(self.writer_symbols, 0, 108, 15, align=ALIGN_RIGHT, bdcolor=False)
        self.label_player_repeat = Label(self.writer_symbols, 0, 90, 15, align=ALIGN_RIGHT, bdcolor=False)
        self.label_artist = Label(self.writer_small, 20, 0, self.display.width - 1, align=ALIGN_CENTER, bdcolor=False)
        self.label_title = ScrollingLabel(self.writer_large, 42, self.display.width)
        self.message_active = False

        refresh(self.display, clear=True)

    def draw_static(self):
        """Draw elements that do not dynamically change values constantly."""
        if self.message_active:
            self.label_artist.show()
            self.label_title.show()
            return

        # Force current state layout components to redraw when switching views
        self.label_wifi_status.show()
        self.label_ws_status.show()
        self.label_volume.show()
        self.label_player_status.show()
        self.label_player_repeat.show()
        self.label_artist.show()
        self.label_title.show()

    def update_frame(self):
        """
        Called continuously by the DisplayManager loop. 
        Returns True if a redraw happened and display.show() is required.
        """
        if self.label_title.needs_scrolling():
            self.label_title.show()
            return True
        return False

    def set_initial_boot_state(self):
        """Set fallback elements during boot cycles."""
        self._set_title("Jukeplayer Wait...")
        self._set_net_status("WS:CON")
        self._set_player_status("BOOT")

    def update(self, state={}):
        """Updates internal values without individual screen flashes."""
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
        if REPEAT_STATUS in state:
            self._set_repeat_status(state[REPEAT_STATUS])
        if NETWORK_STATUS in state:
            self._set_net_status(state[NETWORK_STATUS])
        
    def _set_net_status(self, status):
        status = status.upper()
        if status == "WS:OK":
            self.label_wifi_status.value("\ue308")
        elif status == "WS:CON":
            self.label_wifi_status.value("\ue63e")
        else:
            self.label_wifi_status.value("\ue648")

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
            
    def _set_repeat_status(self, repeat):
        self.label_player_repeat.value("\ue040" if repeat else "")

    def _set_volume(self, volume):
        self.label_volume.value(f"{volume}%")

    def _set_artist(self, artist):
        self.label_artist.value(artist)

    def _set_title(self, title):
        self.label_title.value(title)

    def show_message(self, text, header="Message"):
        self.message_active = True
        self.label_artist.value(header)
        self.label_title.value(text)

    def clear_message(self):
        self.message_active = False


class MockDisplay(framebuf.FrameBuffer):
    def __init__(self, buf, w, h, fmt):
        super().__init__(buf, w, h, fmt)
        self.width = w
        self.height = h

class ScrollingLabel:
    def __init__(self, writer, row, width, gap=30):
        self.writer = writer
        self.device = writer.device
        self.row = row
        self.width = width
        self.height = writer.height
        self.gap = gap
        self.text = ""
        self.text_width = 0
        self.scroll_x = 0
        self.text_fb = None
        self._fb_buf = None
        self._fb_capacity = 0

    def value(self, text):
        if self.text != text:
            self.text = str(text)
            self.text_width = self.writer.stringlen(self.text)
            self.scroll_x = 0
            self._update_buffer()
            self.show()

    def needs_scrolling(self):
        return self.text_fb is not None and self.text_width > self.width
               
    def _update_buffer(self):
        if self.text_width > self.width:
            fb_width = self.text_width + self.gap
            fb_height = ((self.writer.font.height() + 7) // 8) * 8
            fb_size = (fb_height // 8) * fb_width

            if self._fb_buf is None or fb_width > self._fb_capacity:
                self._fb_buf = bytearray(fb_size)
                self._fb_capacity = fb_width

            self.text_fb = MockDisplay(self._fb_buf, fb_width, fb_height, framebuf.MONO_VLSB)
            self.text_fb.fill(0)

            # Writer keeps internal state per device id, so create a fresh one
            # for the offscreen buffer instead of reusing the display writer.
            temp_writer = Writer(self.text_fb, self.writer.font, verbose=False)
            temp_writer.set_textpos(self.text_fb, 0, 0)
            temp_writer.printstring(self.text)
        else:
            self.text_fb = None


    def show(self):
        if not self.text:
            self.device.fill_rect(0, self.row, self.width, self.height, 0)
            return
            
        # Draw background blanking
        self.device.fill_rect(0, self.row, self.width, self.height, 0)
        
        if self.text_width <= self.width:
            # Fits perfectly, center it
            start_x = (self.width - self.text_width) // 2
            self.writer.set_textpos(self.device, self.row, start_x)
            self.writer.printstring(self.text)
        elif self.text_fb is not None:
            # Draw scrolling offset
            fb_width = self.text_width + self.gap
            self.device.blit(self.text_fb, -self.scroll_x, self.row)
            
            if -self.scroll_x + fb_width < self.width:
                 self.device.blit(self.text_fb, -self.scroll_x + fb_width, self.row)
                 
            self.scroll_x += 2
            if self.scroll_x >= fb_width:
                self.scroll_x = 0

