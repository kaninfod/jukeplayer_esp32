import asyncio
import machine
from jukeplayer.gui.core.writer import Writer
from jukeplayer.gui.fonts import geistmonobold18, geistmonobold14, material_subset
from machine import I2C, Pin
import jukeplayer.drivers.ssd1306.ssd1306 as ssd1306

class OLEDScroller:
    def __init__(self, config):
        """
        Initializes the OLED scrolling manager.
        Expects config['oled'] to have 'sda', 'scl', 'width', 'height'.
        """
        self.width = config.get("width", 128)
        self.height = config.get("height", 64)
        sda_pin = config.get("sda", 32)
        scl_pin = config.get("scl", 33)
        
        self.i2c = I2C(0, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=400000)
        self.display = ssd1306.SSD1306_I2C(self.width, self.height, self.i2c)
        
        # Initialize Writer for nano-gui
        self.writer = Writer(self.display, geistmonobold18, verbose=False)
        self.font = geistmonobold18
        
        self.status_writer = Writer(self.display, geistmonobold14, verbose=False)
        
        self.test_writer = Writer(self.display, material_subset, verbose=False)
        self.test = "TEST: 0123456789"

        self.current_text = "Idle..."
        self.text_width = self.writer.stringlen(self.current_text)
        
        self.net_status = "NET: WAIT"
        self.player_status = "STOP"
        
        self.text_fb = None
        self.gap = 30
        
        # Scrolling state
        self.scroll_x = 0
        self.scroll_task = None
        self.is_running = False

    def _update_text_buffer(self):
        """Renders the text to an offscreen buffer if it exceeds width."""
        import framebuf
        
        class MockDisplay(framebuf.FrameBuffer):
            def __init__(self, buf, w, h, fmt):
                super().__init__(buf, w, h, fmt)
                self.width = w
                self.height = h

        if self.text_width > self.width:
            fb_width = self.text_width + self.gap
            
            # Allocate buffer ONLY for the height of the font to save memory and avoid overwriting the top of the screen
            # VLSB needs height in multiples of 8
            fb_height = ((self.font.height() + 7) // 8) * 8
            
            buf = bytearray((fb_height // 8) * fb_width)
            self.text_fb = MockDisplay(buf, fb_width, fb_height, framebuf.MONO_VLSB)
            self.text_fb.fill(0)
            
            # Temporary writer for offscreen buffer (draw at y=0 inside the buffer)
            temp_writer = Writer(self.text_fb, self.font, verbose=False)
            temp_writer.set_textpos(self.text_fb, 0, 0)
            temp_writer.printstring(self.current_text)
        else:
            self.text_fb = None

    def set_net_status(self, status):
        """Update network status (e.g. WiFi/WS state)."""
        self.net_status = status

    def set_player_status(self, status):
        """Update playback status."""
        self.player_status = status
        
    def set_text(self, text):
        """Update the text to display & scroll."""
        if self.current_text != text:
            self.current_text = text
            self.text_width = self.writer.stringlen(self.current_text)
            self.scroll_x = 0  # Reset offset when text changes
            self._update_text_buffer()

    def _get_player_icon(self):
        """Map player status string to material subset icon."""
        status = self.player_status.upper()
        if status == "PLAY":
            return "\ue037"
        elif status == "STOP":
            return "\ue047"
        elif status == "PAUSE":
            return "\ue034"
        return "" # Empty or default if unknown

    def _get_net_icon(self):
        """Map network status string to material subset icon."""
        status = self.net_status.upper()
        if status == "WS:OK":
            return "\ue308"
        elif status == "WS:CON":
            return "\ue63e"
        elif status == "WS:ERR":
            return "\ue648"
        return "\ue648" # Default to no wifi

    async def _scroll_loop(self):
        """Background task for updating display frames."""
        y_pos = self.height - self.font.height() - 2 # Push scrolling text to bottom                                                                                        
        # Initial render of the start text
        self._update_text_buffer()
        
        while self.is_running:
            self.display.fill(0)
            
            # Draw Status Bar (Top)
            net_icon = self._get_net_icon()
            self.test_writer.set_textpos(self.display, 0, 0)
            self.test_writer.printstring(net_icon)
            
            player_icon = self._get_player_icon()
            player_len = self.test_writer.stringlen(player_icon)
            self.test_writer.set_textpos(self.display, 0, self.width - player_len)
            self.test_writer.printstring(player_icon)
            
            # Separator Line
            self.display.hline(0, 14, self.width, 1)
            
            if self.text_width <= self.width:
                # Text fits completely, center it
                start_x = (self.width - self.text_width) // 2
                self.writer.set_textpos(self.display, y_pos, start_x)
                self.writer.printstring(self.current_text)
                self.display.show()
                await asyncio.sleep(1) # Block mostly since no scrolling is needed
            elif self.text_fb is not None:
                # Text exceeds width, use pre-rendered offscreen buffer and blit
                fb_width = self.text_width + self.gap
                
                # Draw main offset string at the bottom
                self.display.blit(self.text_fb, -self.scroll_x, y_pos)
                
                # Draw seamlessly wrapping text on the right
                if -self.scroll_x + fb_width < self.width:
                     self.display.blit(self.text_fb, -self.scroll_x + fb_width, y_pos)
                     
                self.display.show()
                
                # Update offset
                self.scroll_x += 2 # Number of pixels to shift per frame
                if self.scroll_x >= fb_width:
                    self.scroll_x = 0
                
                await asyncio.sleep(0.05) # Loop delay acts as scroll speed


    def start(self):
        """Starts the scrolling task if it isn't running."""
        if not self.is_running:
            self.is_running = True
            self.scroll_task = asyncio.create_task(self._scroll_loop())
            
    def stop(self):
        """Stops the scrolling task."""
        self.is_running = False
        if self.scroll_task:
            self.scroll_task.cancel()
            self.scroll_task = None
