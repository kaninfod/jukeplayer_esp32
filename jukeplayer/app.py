from jukeplayer.core.state_constants import *
from jukeplayer.core.logger import log
from jukeplayer.hardware.hardware_factory import HardwareFactory
from jukeplayer.core.ws_events import AsyncWebsocketClient
from jukeplayer.services.ws_service import  WSService
from jukeplayer.services.hardware_service import HardwareService
import asyncio
from jukeplayer.core.app_state import AppState


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

    def _heap_mark(self, stage, collect=True):
        """Temporary heap checkpoint logger for boot diagnostics."""
        import gc
        if collect:
            gc.collect()
        self.logger.info(
            f"[HEAP] {stage}: free={gc.mem_free()} alloc={gc.mem_alloc()}"
        )
    
    def __init__(self):
        self.logger = log
        self.logger.info("Initializing JukeBoxApp...")
        #self._heap_mark("boot:start")
        
        self.config = load_config()
        
        factory = HardwareFactory(self.config)

        self.hw_service = HardwareService(self)

        # Initialize MQTT service
        self.mqtt_service = HAMQTTService(self)
        self.mqtt_start_delay_s = int(self.config.get("mqtt", {}).get("start_delay_s", 6))

        self.state = AppState()
        self.nfc = factory.get_nfc()
        self.pushbuttons = factory.get_pushbuttons()
        self.hw_service.assign_button_handlers()

        self.display = factory.get_display(app_state=self.state)
        self.display.start()

        self.encoder = factory.get_encoder()
        self.encoder.add_listener(self.hw_service.on_encoder_change)
        
        self.debounce_task = None

        self.state.subscribe(self.display.update)
        self.state.subscribe(self.mqtt_service.publish_snapshot)

        self.leds = {}
        self.leds = factory.get_leds()
        self.leds["red"].blink(interval_ms=200)  # Turn on red LED to indicate booting
        self.leds["green"].turn_off()
        self.leds["blue"].turn_off()

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
        self._heap_mark("boot:end")
        
    async def run(self):
        """Main application loop using async pattern."""
        import asyncio, gc

        self.logger.info("Waiting 3 seconds before WebSocket connection...")
        self._heap_mark("run:before_wait")
        await asyncio.sleep(3)
        
        gc.collect() 
        self._heap_mark("run:before_task_start", collect=False)
        
        # Start WS first, then stagger MQTT to reduce boot-time allocation spikes.
        ws_task = asyncio.create_task(self._websocket_loop())
        telemetry_task = asyncio.create_task(self._telemetry_loop())

        if self.mqtt_service.enabled:
            self.logger.info(
                f"[BOOT] Delaying MQTT start by {self.mqtt_start_delay_s}s to prioritize WS connection"
            )
            await asyncio.sleep(self.mqtt_start_delay_s)
            mqtt_task = asyncio.create_task(self.mqtt_service.run())
            await asyncio.gather(ws_task, telemetry_task, mqtt_task)
            return

        await asyncio.gather(ws_task, telemetry_task)

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
                        self.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})
                        try:
                            if hasattr(self, 'ws') and self.ws:
                                await self.ws.close()
                            self.ws = None
                        except Exception:
                            pass
                        break
                
                # Connection closed, prepare to reconnect
                self.logger.info(f"[RECONNECT] WebSocket connection lost")
                self.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})
                
            except KeyboardInterrupt:
                # Allow keyboard interrupt to propagate
                raise
            except Exception as e:
                self.logger.info(f"WebSocket error: {e}")
                self.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})
            
            # Exponential backoff on reconnect (2s → 4s → 8s → ... → 30s)
            self.logger.info(f"Reconnecting in {reconnect_delay}s...")
            self.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)


    async def _telemetry_loop(self):
        """Handle background telemetry and timed tasks."""
        import time, asyncio

        logger = getattr(self, 'logger', log)

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
                logger.info(f"Telemetry loop error: {e}")
                await asyncio.sleep(1)

    def _log_memory_usage(self):
        """Log current memory usage in KB."""
        import gc
        logger = getattr(self, 'logger', log)
        try:
            free_kb = gc.mem_free() // 1024
            alloc_kb = gc.mem_alloc() // 1024
            total_kb = free_kb + alloc_kb

            # This integer math will now work better because the scale is smaller
            used_pct = (alloc_kb * 100) // total_kb if total_kb > 0 else 0

            self.state.set({MEMORY_USAGE: used_pct, CLIENT_ID: self.client_id or ""})
            logger.info(f"[MEM] Free: {free_kb} KB | Used: {used_pct}% ({alloc_kb} KB allocated)")
        except Exception as e:
            logger.info(f"[MEM] Error reading memory: {e}")

async def main():
    """Entry point for async app."""
    app = JukeBoxApp()
    await app.run()


if __name__ == "__main__":
    log.info(f"Starting Jukebox app...")
    import asyncio
    asyncio.run(main())
