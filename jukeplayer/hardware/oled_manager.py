import asyncio
from jukeplayer.nanogui.core.writer import Writer
from jukeplayer.nanogui.core.nanogui import refresh
from jukeplayer.nanogui.widgets.label import Label, ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT
from jukeplayer.nanogui.fonts import geistmonobold18, geistmonobold14, material_subset
from machine import I2C, Pin
import jukeplayer.hardware.ssd1306.ssd1306 as ssd1306

class DisplayManager:
    def __init__(self, i2c):
        self.width = 128
        self.height = 64
        self.display = ssd1306.SSD1306_I2C(self.width, self.height, i2c)
        
        self.writers = (
            Writer(self.display, material_subset, verbose=False),
            Writer(self.display, geistmonobold14, verbose=False),
            Writer(self.display, geistmonobold14, verbose=False),
            Writer(self.display, geistmonobold18, verbose=False)
        )

        self.screens = {
            "playing": PlayingScreen(self.display, self.writers),
            "encoding": EncodingScreen(self.display, self.writers)
        }

        self.current_screen = self.screens["playing"]
        self.current_screen.draw_static()
        
        self.scroll_task = None
        self.is_running = False

    def switch_layout(self, layout_name):
        """Thread-safe mechanism to swap the visual context."""
        if layout_name in self.screens:
            self.current_screen = self.screens[layout_name]
            self.current_screen.draw_static()
            self.display.show()

    def update_display(self, state):
        """Pass application state straight down to the current visible screen."""
        self.current_screen.update(state)
        self.display.show()

    async def _scroll_loop(self):
        while self.is_running:
            # Let the current view animate itself if it needs to
            redraw_needed = self.current_screen.update_frame()
            if redraw_needed:
                self.display.show()
                await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(0.25) # Throttle loop if screen is resting

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.scroll_task = asyncio.create_task(self._scroll_loop())

    



class OLEDScroller:
    def __init__(self, i2c):
        """
        Initializes the OLED manager with a structured label layout based on nano-gui.
        """
        self.width = 128
        self.height = 64
        
        self.i2c = i2c
        self.display = ssd1306.SSD1306_I2C(self.width, self.height, self.i2c)
        
        # 1. Initialize Writers for different regions
        self.writer_symbols = Writer(self.display, material_subset, verbose=False)
        self.writer_volume = Writer(self.display, geistmonobold14, verbose=False)
        self.writer_artist = Writer(self.display, geistmonobold14, verbose=False)
        self.writer_title = Writer(self.display, geistmonobold18, verbose=False)
        
        refresh(self.display, clear=True)
        
        # 2. Define Layout & Labels (using nano-gui Label)
        # Status line at top (row 0)
        # For symbols, width = 15 or 16. To avoid nanogui >= bounds error, use widths that sum to < 128
        self.label_wifi_status = Label(self.writer_symbols, 0, 0, 16, align=ALIGN_LEFT, bdcolor=False)
        self.label_ws_status = Label(self.writer_symbols, 0, 16, 16, align=ALIGN_LEFT, bdcolor=False)
        self.label_volume = Label(self.writer_volume, 0, 32, 55, align=ALIGN_CENTER, bdcolor=False)
        self.label_player_status = Label(self.writer_symbols, 0, 108, 15, align=ALIGN_RIGHT, bdcolor=False)
        self.label_player_repeat = Label(self.writer_symbols, 0, 90, 15, align=ALIGN_RIGHT, bdcolor=False)
        
        # Artist line (above scrolling text, row 20)
        # Avoid nanogui bounds error: max width is 127
        self.label_artist = Label(self.writer_artist, 20, 0, self.width - 1, align=ALIGN_CENTER, bdcolor=False)
        
        # Title line (scrolling text, bottom, row 44)
        self.label_title = ScrollingLabel(self.writer_title, 42, self.width)
        
        # Draw Separator Line initially
        #self.display.hline(0, 14, self.width, 1)
        self.display.show()
        
        # State Tracking
        self.scroll_task = None
        self.is_running = False

    def update(self, state):
        state ={
            "net_status": "WS:OK",
            "volume": 50,
            "player_status": "Play",
            "player_repeat": True,
            "artist": "Artist",
            "title": "Title",
        }

        if "volume" in state:
            self.set_volume(state["volume"])
        if "artist" in state:
            self.set_artist(state["artist"])
        if "title" in state:
            self.set_text(state["title"])
        if "player_status" in state:
            self.set_player_status(state["player_status"])
        if "player_repeat" in state:
            self.set_repeat_status(state["player_repeat"])
        if "net_status" in state:
            self.set_net_status(state["net_status"])        
        

    def set_net_status(self, status):
        """Map network status to symbols and update labels."""
        status = status.upper()
        if status == "WS:OK":
            self.label_wifi_status.value("\ue308") #("\ue63e")  # Wifi
        elif status == "WS:CON":
            self.label_wifi_status.value("\ue63e")  # Wifi
        else:
            self.label_wifi_status.value("\ue648")  # No Wifi
        self.display.show()

    def set_player_status(self, status):
        """Map playback status to symbol and update."""
        status = status.upper()
        if status == "PLAY":
            self.label_player_status.value("\ue037")
        elif status == "STOP" or status == "BOOT":
            self.label_player_status.value("\ue047")
        elif status == "PAUSE":
            self.label_player_status.value("\ue034")
        else:
            self.label_player_status.value("")   
        self.display.show()
            
    def set_repeat_status(self, repeat):
        """Update repeat status symbol."""
        if repeat:
            self.label_player_repeat.value("\ue040")    
        else:
            self.label_player_repeat.value("")
        self.display.show()

    def set_volume(self, volume):
        self.label_volume.value(f"{volume}%")
        self.display.show()

    def set_artist(self, artist):
        self.label_artist.value(artist)
        self.display.show()
        
    def set_text(self, title):
        """Update the title scrolling label."""
        self.label_title.value(title)
        self.display.show()

    async def _scroll_loop(self):
        """Background task for updating display frames."""
        while self.is_running:
            if self.label_title.text_fb is not None and self.label_title.text_width > self.width:
                self.label_title.show()
                self.display.show()
                await asyncio.sleep(0.05) # Loop fast for smooth 20fps scrolling
            else:
                await asyncio.sleep(0.5) # Block mostly since no scrolling is needed

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.scroll_task = asyncio.create_task(self._scroll_loop())
            self.set_text("Jukeplayer Wait...")
            self.set_net_status("WS:CON")
            self.set_player_status("BOOT")
            
    def stop(self):
        self.is_running = False
        if self.scroll_task:
            self.scroll_task.cancel()
            self.scroll_task = None



class ScrollingLabel:
    """A label class that generates an offscreen buffer for smooth pixel-by-pixel scrolling."""
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

    def value(self, text):
        if self.text != text:
            self.text = str(text)
            self.text_width = self.writer.stringlen(self.text)
            self.scroll_x = 0
            self._update_buffer()
            self.show()

    def _update_buffer(self):
        import framebuf
        
        class MockDisplay(framebuf.FrameBuffer):
            def __init__(self, buf, w, h, fmt):
                super().__init__(buf, w, h, fmt)
                self.width = w
                self.height = h

        if self.text_width > self.width:
            fb_width = self.text_width + self.gap
            # Buffer height must be multiples of 8
            fb_height = ((self.writer.font.height() + 7) // 8) * 8
            
            buf = bytearray((fb_height // 8) * fb_width)
            self.text_fb = MockDisplay(buf, fb_width, fb_height, framebuf.MONO_VLSB)
            self.text_fb.fill(0)
            
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