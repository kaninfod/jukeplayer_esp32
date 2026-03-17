import st7735
import sysfont
import seriffont
import time
from machine import Pin

class Display:
    """Wrapper for ST7735 TFT display with jukebox-specific layout.
    
    Manages display rendering and current playback state.
    """
    
    def __init__(self, spi, dc_pin=2, reset_pin=12, cs_pin=15):
        """Initialize display with rotated orientation on separate SPI2/VSPI bus.
        
        Args:
            spi: SPI2 (VSPI) instance (separate from NFC which uses SPI1/HSPI)
            dc_pin: Data/Command pin (GPIO 2)
            reset_pin: Reset pin (GPIO 12)
            cs_pin: Chip Select pin (GPIO 15)
        """
        self.tft = st7735.TFT(spi, dc_pin, reset_pin, cs_pin)
        self.tft.initr()
        self.tft.rotation(3)  # 90 degrees counterclockwise
        self.tft.rgb(False)
        self.font = sysfont.sysfont
        #self.font = seriffont.seriffont
        
        # Track current playback state
        self.current_track = {"title": "", "album": "", "artist": ""}
        self.current_status = ""
        self.current_volume = 0
    
    def update_track(self, track_info):
        """Update display with track information.
        
        Args:
            track_info: Dict with keys: title, album, artist, status, volume
        """
        self.current_track = {
            "title": track_info.get("title", ""),
            "album": track_info.get("album", ""),
            "artist": track_info.get("artist", "")
        }
        self.current_status = track_info.get("status", "")
        self.current_volume = track_info.get("volume", 0)
        self._render_track()
    
    def update_volume(self, volume):
        """Update volume display only.
        
        Args:
            volume: Volume level 0-100
        """
        self.current_volume = volume
        self._render_track()
    
    def _render_track(self):
        """Internal method to render track display with current state."""
        self.tft.fill(st7735.TFT.BLACK)
        
        # Title
        self.tft.text((5, 5), str(self.current_track["title"])[:35], st7735.TFT.YELLOW, self.font, 1)
        
        # Album and artist
        self.tft.text((5, 25), str(self.current_track["album"])[:35], st7735.TFT.WHITE, self.font, 1)
        self.tft.text((5, 45), str(self.current_track["artist"])[:35], st7735.TFT.WHITE, self.font, 1)
        
        # Status on left, volume on right, same line
        status_text = self.current_status.upper()[:8] if self.current_status else "UNKNOWN"
        vol_text = f"VOL:{self.current_volume}%"
        self.tft.text((5, 70), status_text, st7735.TFT.CYAN, self.font, 1)
        self.tft.text((95, 70), vol_text, st7735.TFT.CYAN, self.font, 1)
        
        # Time display
        t = time.gmtime()
        formatted_time = f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
        self.tft.text((5, 90), formatted_time, st7735.TFT.CYAN, self.font, 1)
        
        # Album and artist
        self.tft.text((5, 25), str(self.current_track["album"])[:35], st7735.TFT.WHITE, self.font, 1)
        self.tft.text((5, 45), str(self.current_track["artist"])[:35], st7735.TFT.WHITE, self.font, 1)
        
        # Status on left, volume on right, same line
        status_text = self.current_status.upper()[:8] if self.current_status else "UNKNOWN"
        vol_text = f"VOL:{self.current_volume}%"
        self.tft.text((5, 70), status_text, st7735.TFT.CYAN, self.font, 1)
        self.tft.text((95, 70), vol_text, st7735.TFT.CYAN, self.font, 1)
        
        # Time display
        t = time.gmtime()
        formatted_time = f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
        self.tft.text((5, 90), formatted_time, st7735.TFT.CYAN, self.font, 1)
        
        # Album and artist
        self.tft.text((5, 25), str(self.current_track["album"])[:35], st7735.TFT.WHITE, self.font, 1)
        self.tft.text((5, 45), str(self.current_track["artist"])[:35], st7735.TFT.WHITE, self.font, 1)
        
        # Status on left, volume on right, same line
        status_text = self.current_status.upper()[:8] if self.current_status else "UNKNOWN"
        vol_text = f"VOL:{self.current_volume}%"
        self.tft.text((5, 70), status_text, st7735.TFT.CYAN, self.font, 1)
        self.tft.text((95, 70), vol_text, st7735.TFT.CYAN, self.font, 1)
        
        # Time display
        t = time.gmtime()
        formatted_time = f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
        self.tft.text((5, 90), formatted_time, st7735.TFT.CYAN, self.font, 1)
    
    def update(self, title, album="", artist="", status="", volume=0, color=st7735.TFT.YELLOW):
        """DEPRECATED: Update display with track info and playback status.
        
        Use update_track() or update_volume() instead.
        
        Args:
            title: Track title
            album: Album name
            artist: Artist name
            status: Playback status ("playing", "paused", "stopped", or "")
            volume: Volume level 0-100
            color: Title color (unused, kept for backwards compatibility)
        """
        self.update_track({
            "title": title,
            "album": album,
            "artist": artist,
            "status": status,
            "volume": volume
        })
    
    def show_status(self, status_msg, details="", color=st7735.TFT.YELLOW):
        """Show a status message (e.g., connecting, error)."""
        self.tft.fill(st7735.TFT.BLACK)
        self.tft.text((5, 10), "JUKEBOX", st7735.TFT.GRAY, self.font, 1)
        self.tft.text((5, 30), str(status_msg)[:35], color, self.font, 1)
        self.tft.text((5, 50), str(details)[:35], st7735.TFT.WHITE, self.font, 1)
