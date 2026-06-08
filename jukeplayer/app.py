from jukeplayer.lib.nfc_reader import NFCReader
from jukeplayer.lib.input_controller import InputController
from jukeplayer.lib.ky_040 import KY040Controller
from jukeplayer.lib.ws_events import AsyncWebsocketClient
from jukeplayer.gui.oled_manager import OLEDScroller
from jukeplayer.lib.ws_service import  WSService
from jukeplayer.lib.hardware_bus import HardwareBus
from jukeplayer.lib.hardware_service import HardwareService

__version__ = "FILESYSTEM_v1"  # Change to "FILESYSTEM_v1" in your device copy


from jukeplayer.lib.logger import log

def load_config():
    """Load configuration from config.json file at device root.
    
    NOTE: config.json is NOT frozen with the application, so it can be
    modified on the device without rebuilding the frozen firmware.
    The file must be located at the root of the device filesystem.
    """
    import json

    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        log.info(f"Config loaded - Client: {config['client']['name']}")
        return config
    except Exception as e:
        log.error(f"FATAL ERROR loading config.json in main.py: {e}")
        # Return fallback just so object creation doesn't crash here 
        # (Though boot.py would have frozen first if it really doesn't exist)
        return {}


class JukeBoxApp:
    """Main application orchestrating display, NFC, API, and WebSocket."""
    
    def __init__(self):
        log.info("Initializing Hardware...")
        
        # Load configuration
        self.config = load_config()
        self.bus = HardwareBus(self.config)

        self.state = {
            "title": "Idle",
            "artist": "",
            "status": "STOP",
            "volume": 0,
            "repeat": False,
            "nfc_encoding_state": None,
            "nfc_encoding_album_id": None

        }
         
        # Check for dummy mode (disables all hardware)
        self.dummy_mode = self.config.get("dummy_mode", False)
        if self.dummy_mode:
            log.info(f"⚠️  DUMMY MODE ENABLED - All hardware disabled")
        
        # Extract backend settings from config
        backend_ip = self.config["backend"]["ip"]
        backend_port = self.config["backend"]["port"]
        ws_path = "/ws/mediaplayer/events?detail=minimal"
        self.server_url = f"ws://{backend_ip}:{backend_port}{ws_path}"
        
        # Extract pin configs
        display_cfg = self.config.get("oled", {})
        encoder_cfg = self.config.get("encoder", {})
        buttons_cfg = self.config.get("buttons", {})

        self.ws_service = WSService(self)
        self.logger = log

        

        # Initialize OLED Scroller (which sets up I2C internally based on config.oled)
        self.logger.info("Initializing OLED Scroller...")
        try:
            self.oled = OLEDScroller(display_cfg)
            self.oled.start()

        except Exception as e:
            self.logger.error(f"Failed to init OLED: {e}")
            self.oled = None
        
        try:
            self.nfc = NFCReader(self.bus.get_nfc_spi())
        except Exception as e:
            self.logger.info(f"WARNING - NFC reader initialization failed: {e}")
            self.nfc = None
        

        self.inputs = InputController(
            button_pins=self.bus.get_button_pins(),
            debounce_ms=buttons_cfg.get('debounce_ms', 100),
            dummy_mode=self.dummy_mode)
        
        self.encoder = KY040Controller(
            clk_pin=encoder_cfg.get('clk', 27), 
            dt_pin=encoder_cfg.get('dt', 16), 
            dummy_mode=self.dummy_mode)
        
        # WebSocket client (library-based)
        self.ws = AsyncWebsocketClient(5)  # socket_delay_ms as positional argument
        
        # Client registration state
        self.client_id = None
        
        # NFC encoding state (used to suppress normal reads during encoding)
        self.nfc_encoding_album_id = None  # Set when encoding mode is active
        
        # Memory monitoring (log every 30 seconds)
        self.last_memory_log = 0
        self.memory_log_interval = 30000  # Log memory every 30 seconds

    async def run(self):
        """Main application loop using async pattern."""
        
        # Wait briefly for backend and network to stabilize before WebSocket connection
        # This reduces failed handshake attempts on first boot
        import asyncio, gc

        self.logger.info("Waiting 3 seconds before WebSocket connection...")
        await asyncio.sleep(3)
        
        gc.collect()  # Free memory before API call
        
        # Main event loop - run WebSocket and input handling concurrently
        await asyncio.gather(
            self._websocket_loop(),
            self._hw_loop()
        )

    async def _websocket_loop(self):
        """Manage WebSocket connection and receive updates."""
        import gc, asyncio

        reconnect_delay = 2
        max_reconnect_delay = 30
        
        while True:
            try:
                await self.ws_service.connect_websocket()
                reconnect_delay = 2  # Reset delay on successful connection
                
                # Read messages from server
                while await self.ws.open():
                    try:
                        gc.collect()  # Defragment heap before frame buffer allocation
                        data = await self.ws.recv()
                        if data:
                            await self.ws_service.message_handler(data)
                            data = None
                        await asyncio.sleep_ms(50)
                    except Exception as e:
                        self.logger.info(f"WebSocket recv error: {e}")
                        try:
                            if hasattr(self, 'ws') and self.ws:
                                await self.ws.close()
                            self.ws = None
                        except Exception:
                            pass
                        break
                
                # Connection closed, prepare to reconnect
                self.logger.info(f"[RECONNECT] WebSocket connection lost")
                
            except KeyboardInterrupt:
                # Allow keyboard interrupt to propagate
                raise
            except Exception as e:
                self.logger.info(f"WebSocket error: {e}")
            
            # Exponential backoff on reconnect (2s → 4s → 8s → ... → 30s)
            self.logger.info(f"Reconnecting in {reconnect_delay}s...")
            if self.oled:
                self.oled.set_net_status("WS:ERR")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    async def _hw_loop(self):
        """Handle button presses, microswitch, rotary encoder, and potentiometer input."""
        import time, asyncio
        hw_service = HardwareService(self)
        while True:
            try:
                # Check all inputs (buttons and microswitch)
                input_name = self.inputs.check_inputs()
                if input_name:
                    if input_name == InputController.NFC_CARD:
                        await hw_service.handle_microswitch_press()
                    else:
                        await hw_service.handle_button_press(input_name)
                
                # Check if encoder volume changed (flag set by IRQ handler)
                if self.encoder.volume_changed:
                    # Update display immediately (local feedback)
                    current_volume = self.encoder.get_value()
                    log.info(f"Display updated to {current_volume}% (local encoder change)")
                    
                    # Check if debounce window has elapsed to send API call
                    if self.encoder.should_send_api_update(debounce_ms=300):
                        self.encoder.volume_changed = False  # Clear flag after sending
                        await hw_service.handle_volume_change(current_volume)
                
                # Memory monitoring every 30 seconds
                now = time.ticks_ms()
                if time.ticks_diff(now, self.last_memory_log) >= self.memory_log_interval:
                    self.last_memory_log = now
                    self._log_memory_usage()
                
                # Yield to event loop frequently to prevent watchdog timeout
                await asyncio.sleep_ms(50)
                
            except KeyboardInterrupt:
                # Allow keyboard interrupt to propagate
                raise
            except Exception as e:
                self.logger.info(f"Button/NFC/Encoder/Pot loop error: {e}")
                await asyncio.sleep(1)

    def _log_memory_usage(self):
        """Log current memory usage (free and allocated)."""
        import gc
        try:
            free = gc.mem_free()
            alloc = gc.mem_alloc()
            total = free + alloc
            used_pct = (alloc * 100) // total if total > 0 else 0
            self.logger.info(f"[MEM] Free: {free} bytes | Alloc: {alloc} bytes | Total: {total} bytes | Used: {used_pct}%")
        except Exception as e:
            self.logger.info(f"[MEM] Error reading memory: {e}")



async def main():
    """Entry point for async app."""
    app = JukeBoxApp()
    await app.run()


if __name__ == "__main__":
    log.info(f"Starting Jukebox app...")
    import asyncio
    asyncio.run(main())
