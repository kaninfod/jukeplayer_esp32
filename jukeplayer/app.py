from jukeplayer.core.logger import log
from jukeplayer.hardware.hardware_factory import HardwareFactory
from jukeplayer.core.ws_events import AsyncWebsocketClient
from jukeplayer.services.ws_service import  WSService
from jukeplayer.services.hardware_service import HardwareService
import asyncio
# import time

from jukeplayer.mqtt.ha_mqtt_devices import HAMQTTService

__version__ = "FILESYSTEM_v1"  # Change to "FILESYSTEM_v1" in your device copy

def load_config():
    """Load configuration from config.json file at device root."""
    import json

    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        log.info(f"Config loaded - Client: {config['client']['name']}")
        return config
    except Exception as e:
        log.error(f"FATAL ERROR loading config.json in main.py: {e}")
        return {}


class JukeBoxApp:
    """Main application orchestrating display, NFC, API, and WebSocket."""
    
    def __init__(self):
        self.logger = log
        self.logger.info("Initializing JukeBoxApp...")
        
        self.config = load_config()
        
        factory = HardwareFactory(self.config)

        self.hw_service = HardwareService(self)

        self.oled = factory.get_oled()
        self.nfc = factory.get_nfc()
        
        self.pushbuttons = factory.get_pushbuttons()
        self.hw_service.assign_button_handlers()

        self.encoder = factory.get_encoder()
        self.encoder.add_listener(self.hw_service.on_encoder_change)
        
        self.debounce_task = None

        self.oled.start()

        self.state = {
            "title": "Idle",
            "artist": "",
            "album": "",
            "track": "",
            "status": "STOP",
            "player_status": "idle",
            "volume": 0,
            "repeat": False,
            "repeat_status": False,
            "network_status": "ws_connecting",
            "mute_status": False,
            "client_id": "",
            "memory_usage": 0,
            "nfc_encoding_state": None,
            "nfc_encoding_album_id": None
        }
         
        # Extract backend settings from config
        backend_ip = self.config["backend"]["ip"]
        backend_port = self.config["backend"]["port"]
        ws_path = "/ws/mediaplayer/events?detail=minimal"
        self.server_url = f"ws://{backend_ip}:{backend_port}{ws_path}"

        self.ws_service = WSService(self)

        # WebSocket client
        self.ws = AsyncWebsocketClient(5)  
        
        # Client registration state
        self.client_id = None
        
        # NFC encoding state (used to suppress normal reads during encoding)
        self.nfc_encoding_album_id = None  
        
        # Memory monitoring (log every 30 seconds)
        self.last_memory_log = 0
        self.memory_log_interval = 30000  # Log memory every 30 seconds
        
        # Initialize MQTT service
        self.mqtt_service = HAMQTTService(self)
            
    async def run(self):
        """Main application loop using async pattern."""
        import asyncio, gc

        self.logger.info("Waiting 3 seconds before WebSocket connection...")
        await asyncio.sleep(3)
        
        gc.collect() 
        
        # Main event loop - run WebSocket and input handling concurrently
        tasks = [
            self._websocket_loop(),
            self._telemetry_loop()
        ]
        if self.mqtt_service.enabled:
            tasks.append(self.mqtt_service.run())
            
        await asyncio.gather(*tasks)

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
            
            self.oled.set_net_status("WS:ERR")
            self.state["network_status"] = "ws_error"
            self.mqtt_service.publish_snapshot(reason="ws_error")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)


    async def _telemetry_loop(self):
        """Handle background telemetry and timed tasks."""
        import time, asyncio
        
        while True:
            try:
                # Memory monitoring every 30 seconds
                now = time.ticks_ms()
                if time.ticks_diff(now, self.last_memory_log) >= self.memory_log_interval:
                    self.last_memory_log = now
                    self._log_memory_usage()
                
                # We can sleep longer here now since it isn't checking rapid knob turns
                await asyncio.sleep(1)
                
            except KeyboardInterrupt:
                raise
            except Exception as e:
                self.logger.info(f"Telemetry loop error: {e}")
                await asyncio.sleep(1)

    def _log_memory_usage(self):
        """Log current memory usage (free and allocated)."""
        import gc
        try:
            free = gc.mem_free()
            alloc = gc.mem_alloc()
            total = free + alloc
            used_pct = (alloc * 100) // total if total > 0 else 0
            self.state["memory_usage"] = used_pct
            self.state["client_id"] = self.client_id or ""
            self.logger.info(f"[MEM] Free: {free} bytes | Used: {used_pct}% | Client ID: {self.client_id}")
            self.mqtt_service.publish_snapshot(reason="memory")
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
