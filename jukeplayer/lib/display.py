"""
Display wrapper using nano-gui framework for JukePlayer

Manages display rendering with professional widgets and layout.
Supports live track info updates, volume display, and status messages.

LAZY LOADING: GUI libraries are imported only when first display call is made,
to reduce memory footprint at startup and allow WiFi to initialize properly.
"""

import time




class Display:
    """Nano-GUI based display wrapper for JukePlayer.
    
    Manages display rendering with widgets and current playback state.
    Keeps the same public API as the original st7735.py based implementation.
    
    GUI libraries are lazy-loaded on first use to save memory at startup.
    Can operate in dummy mode for testing without display hardware.
    """
    
    def __init__(self, spi=None, dc_pin=None, reset_pin=None, cs_pin=None, dummy_mode=False):
        """Initialize display without loading GUI libraries yet.
        
        Args:
            spi: Ignored (display already initialized in color_setup.py)
            dc_pin: Ignored (pins already configured in color_setup.py)
            reset_pin: Ignored (pins already configured in color_setup.py)
            cs_pin: Ignored (pins already configured in color_setup.py)
            dummy_mode: If True, don't initialize actual display hardware
        """
        self.dummy_mode = dummy_mode
        if dummy_mode:
            print("LOG: Display initialized in DUMMY MODE (no screen updates)")
        
        # Track current playback state
        self.current_track = {"title": "", "album": "", "artist": ""}
        self.current_status = ""
        self.current_volume = 0
        
        # UI state
        self._initialized = False  # Track if GUI libs have been loaded
        
        # Lazy-loaded modules (None until first use)
        self.ssd = None
        self.refresh = None
        self.CWriter = None
        self.Label = None
        self.font6 = None
        self.BLACK = None
        self.GREEN = None
        self.YELLOW = None
        self.WHITE = None
        self.CYAN = None
        self.RED = None
        
        # Universal 4-label layout - initialized once, reused for all display modes
        # Label 1: Row 0   - Track title / Message title
        # Label 2: Row 12  - Album / Message body
        # Label 3: Row 24  - Artist / (empty in message mode)
        # Label 4: Row 36  - Status + Volume / (empty in message mode)
        self.label1 = None
        self.label2 = None
        self.label3 = None
        self.label4 = None
        self.label5 = None
        self.statusbar = None
        self.led1 = None 
    
    def _lazy_init(self):
        """Lazy-load GUI libraries and initialize universal 4-label layout."""
        if self._initialized:
            return
        
        print("Loading GUI libraries...")
        import gc
        gc.collect()  # Free up memory before loading GUI
        
        # Import with absolute paths (required for frozen modules)
        from jukeplayer.color_setup import ssd, SSD
        from jukeplayer.gui.core.nanogui import refresh
        from jukeplayer.gui.core.writer import CWriter
        from jukeplayer.gui.core.colors import BLACK, GREEN, YELLOW, WHITE, CYAN, RED
        from jukeplayer.gui.widgets.label import Label, ALIGN_LEFT, ALIGN_CENTER
        from jukeplayer.gui.widgets.led import LED
        import jukeplayer.gui.fonts.geistmonobold12 as geistmonobold12
        #import jukeplayer.gui.fonts.geistmonomed12 as geistmonomed12
        
        # Store references
        self.ssd = ssd
        self.refresh = refresh
        self.CWriter = CWriter
        self.Label = Label
        #self.font6 = font6
        self.BLACK = BLACK
        self.GREEN = GREEN
        self.YELLOW = YELLOW
        self.WHITE = WHITE
        self.CYAN = CYAN
        self.RED = RED
        
        # Initialize display
        self.refresh(self.ssd, True)
        
        # Create universal 4-label layout (same labels used for all display modes)
        # Row 0: Label 1 - Track title / Message title (GREEN)
        #regular_writer= self.CWriter(self.ssd, geistmonomed12, self.GREEN, self.BLACK, verbose=False)
        bold_writer= self.CWriter(self.ssd, geistmonobold12, self.WHITE, self.BLACK, verbose=False)
        
        #writer1 = self.CWriter(self.ssd, geistmonomed12, self.GREEN, self.BLACK, verbose=False)
        self.label1 = self.Label(bold_writer, 12, 3, "", align=ALIGN_CENTER)
        
        # Row 12: Label 2 - Album / Message body (WHITE)
        #writer2 = self.CWriter(self.ssd, geistmonomed12, self.WHITE, self.BLACK, verbose=False)
        self.label2 = self.Label(bold_writer, 37, 3, "")
        
        # Row 24: Label 3 - Artist (WHITE)
        #writer3 = self.CWriter(self.ssd, geistmonomed12, self.WHITE, self.BLACK, verbose=False)
        self.label3 = self.Label(bold_writer, 62, 3, "")
        
        # Row 36: Label 4 - Status + Volume (CYAN)
        #writer4 = self.CWriter(self.ssd, geistmonobold12, self.CYAN, self.BLACK, verbose=False)
        self.label4 = self.Label(bold_writer, 112, 3, "", bgcolor=self.WHITE)
        
        # Row 36: Label 5 - Status + Volume (CYAN)
        #writer5 = self.CWriter(self.ssd, geistmonobold12, self.CYAN, self.BLACK, verbose=False)
        self.label5 = self.Label(bold_writer, 110, 100, "", bgcolor=self.WHITE)
        self.led1 = LED(bold_writer, 4, 146, height=12, fgcolor=self.GREEN, bgcolor=self.BLACK, bdcolor=False)          

        self._initialized = True
        print("✓ Display initialized with nano-gui (4-label universal layout)")
        
        # Aggressive garbage collection after loading large GUI libraries
        gc.collect()
        
    
    def update_track(self, track_info):
        """Update display with track information using universal 4-label layout.
        
        Args:
            track_info: Dict with keys: title, album, artist, status, volume
        """
        self._lazy_init()  # Load GUI libs on first use
        
        self.current_track = {
            "title": track_info.get("title", ""),
            "album": track_info.get("album", ""),
            "artist": track_info.get("artist", "")
        }
        self.current_status = track_info.get("status", "")
        self.current_volume = track_info.get("volume", 0)
        
        self.refresh(self.ssd, True)  # Clear screen before updating

        if self.current_track["album"]:

            # Update the 4 universal labels using .value() method
            artist_text = str(self.current_track["artist"]) if self.current_track["artist"] else ""
            self.label1.value(artist_text)  # Artist
            
            album_text = str(self.current_track["album"]) if self.current_track["album"] else ""
            self.label2.value(album_text)  # Album
            
            title_text = str(self.current_track["title"]) if self.current_track["title"] else "Unknown"
            self.label3.value(title_text)  # Track title



        else:
            
            self.label2.value("Scan card for music...") 

                
            
        self.statusbar = self.ssd.rect(0, 108, 160, 20, self.WHITE, True)
        self.led1.show()  
        
        # Label 4: Status
        status_text = self.current_status.upper() if self.current_status else "IDLE"
        self.label4.value(status_text, fgcolor=self.BLACK)
        
        # Label 5: Volume
        vol_text = f"  VOL:{self.current_volume}%"
        self.label5.value(vol_text, fgcolor=self.BLACK)
           
        self.refresh(self.ssd)
    
    def update_volume(self, volume):
        """Update volume display only.
        
        Args:
            volume: Volume level 0-100
        """
        self._lazy_init()  # Load GUI libs on first use
        
        self.current_volume = volume
        vol_text = f"  VOL:{self.current_volume}%"
        #self.refresh(self.ssd, True)  # Clear screen before updating
        # Update label 4 with new volume (keep status text)
        #status_text = self.current_status.upper()[:10] if self.current_status else "IDLE"
        self.label5.value(vol_text, fgcolor=self.BLACK)
        
        self.refresh(self.ssd)
    
    def show_status(self, status_msg, details="", color=None):
        """Show a status message using the universal 4-label layout.
        
        In message mode: Label1=title, Label2=message body, Label3/4=empty
        
        Args:
            status_msg: Primary status message
            details: Secondary details message
            color: Message color (ignored, using fixed green/white for simplicity)
        """
        if self.dummy_mode:
            return  # Dummy mode: skip display updates
        self._lazy_init()  # Load GUI libs on first use
        
        self.refresh(self.ssd, True)  # Clear screen before updating
        # Update label 1 (always show "JUKEBOX" title in green)
        self.label1.value("JUKEBOX")
        
        # Update label 2 (primary status message in white)
        status_text = str(status_msg)[:28]
        self.label2.value(status_text)
        
        # Update label 3 (details message if provided)
        if details:
            detail_text = str(details)[:28]
            self.label3.value(detail_text)
        else:
            self.label3.value("")
        
        # Clear label 4 in message mode
        self.label4.value("")
        
        self.refresh(self.ssd)
    