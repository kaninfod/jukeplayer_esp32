from jukeplayer.core.state_constants import *
from jukeplayer.core.logger import log
from jukeplayer.hardware.hardware_factory import HardwareFactory
from jukeplayer.core.ws_events import AsyncWebsocketClient
from jukeplayer.services.ws_service import  WSService
from jukeplayer.services.hardware_service import HardwareService
import asyncio
from jukeplayer.core.app_state import AppState

__version__ = "FILESYSTEM_v1"  # Change to "FILESYSTEM_v1" in your device copy

def load_config():
    """Load configuration from config.json file at device root."""
    import json

    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        log.info(f"[CFG] loaded — client: {config['client']['name']}")
        return config
    except Exception as e:
        log.error(f"[CFG] fatal error loading config.json: {e}")
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
        self.logger.info("[APP] initializing JukeBoxApp...")
        #self._heap_mark("boot:start")
        
        self.config = load_config()

        # Re-apply logging configuration from the loaded config (idempotent)
        self.logger.configure_from_config(self.config)
        
        factory = HardwareFactory(self.config)

        self.hw_service = HardwareService(self)

        self.state = AppState()
        self.nfc = factory.get_nfc()
        self.pushbuttons = factory.get_pushbuttons()
        self.hw_service.assign_button_handlers()

        self.display = factory.get_display(app_state=self.state)
        self.display.start()
        log.info("[RUN] display started")

        self.encoder = factory.get_encoder()
        # IRQ-safe bridge: the rotary IRQ only sets a ThreadSafeFlag; the
        # encoder task (running in loop context) does the real work.
        self.encoder.add_listener(self.hw_service.encoder_flag_set)
        self.hw_service.start_encoder_task()
        
        self.debounce_task = None

        self.state.subscribe(self.display.update)

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

        self.logger.info("[RUN] entering run loop")
        self.logger.info("Waiting 3 seconds before WebSocket connection...")
        self._heap_mark("run:before_wait")
        await asyncio.sleep(3)
        
        gc.collect() 
        self._heap_mark("run:before_task_start", collect=False)
        
        # Start WS first, then stagger MQTT to reduce boot-time allocation spikes.
        ws_task = asyncio.create_task(self._websocket_loop())
        telemetry_task = asyncio.create_task(self._telemetry_loop())
        log.info("[RUN] tasks started: ws + telemetry")

        # Hardware watchdog, config-gated: armed only after the event loop is
        # running (the boot's blocking WiFi/NTP phase is exempt). A total
        # event-loop freeze — blocking C call, deadlock — now self-recovers
        # via a WDT reset; single-task hangs are bounded by the WS guards.
        # RTC forensics survive the WDT reset, so the next boot reports both
        # the hang and the last heartbeat.
        wd_cfg = self.config.get("hardware", {}).get("watchdog", {})
        if wd_cfg.get("enabled", False):
            import machine
            timeout_ms = max(1000, int(wd_cfg.get("timeout_s", 8)) * 1000)
            self._wdt = machine.WDT(timeout=timeout_ms)
            asyncio.create_task(self._wdt_feed_loop())
            log.debug(f"[WDT] armed, timeout {timeout_ms} ms")
        else:
            log.info("[WDT] disabled by config")

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
                        # Traceback to syslog: the message alone hides the frame
                        # that raised (the __rshift__ error had no trail).
                        import io, sys
                        buf = io.StringIO()
                        sys.print_exception(e, buf)
                        for tb_line in buf.getvalue().split("\n"):
                            if tb_line:
                                self.logger.error(f"[WS] traceback: {tb_line}")
                        self.logger.warn(f"[WS] recv error: {e}")
                        self.logger.info("[WS] recv-error handling: updating state")
                        self.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})
                        self.logger.debug("[WS] recv-error handling: closing socket")
                        try:
                            if hasattr(self, 'ws') and self.ws:
                                # Bounded close: a corrupted/half-dead socket can
                                # hang close() forever, which froze the WS task
                                # before the reconnect could run
                                # (Klangmeister 2026-09-16).
                                await asyncio.wait_for(self.ws.close(), timeout=3)
                            self.ws = None
                            self.logger.debug("[WS] socket closed")
                        except Exception as close_err:
                            self.logger.warn(f"[WS] socket close failed/timed out ({close_err!r}) — discarding client")
                            self.ws = None
                        break
                
                # Connection closed, prepare to reconnect
                self.logger.warn(f"[RECONNECT] WebSocket connection lost")
                self.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})
                
            except KeyboardInterrupt:
                # Allow keyboard interrupt to propagate
                raise
            except Exception as e:
                # Traceback to syslog: same diagnostic as the recv-error path
                import io, sys
                buf = io.StringIO()
                sys.print_exception(e, buf)
                for tb_line in buf.getvalue().split("\n"):
                    if tb_line:
                        self.logger.error(f"[WS] traceback: {tb_line}")
                self.logger.warn(f"[WS] error: {e}")
                self.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})
            
            # Exponential backoff on reconnect (2s → 4s → 8s → ... → 30s)
            self.logger.info(f"[RECONNECT] retrying in {reconnect_delay}s...")
            self.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)


    async def _telemetry_loop(self):
        """Handle background telemetry and timed tasks."""
        import time, asyncio, gc

        logger = getattr(self, 'logger', log)

        while True:
            try:
                # Memory monitoring every 30 seconds
                now = time.ticks_ms()
                if time.ticks_diff(now, self.last_memory_log) >= self.memory_log_interval:
                    self.last_memory_log = now
                    # Traffic-independent GC tick: the recv-loop collect only runs
                    # between received WS frames, so while the backend is silent no
                    # gc.collect() executes anywhere and transient garbage piles up
                    # uncollected (mem_alloc inflates until the next connection event).
                    free_before = gc.mem_free()
                    gc.collect()
                    self._log_memory_usage((gc.mem_free() - free_before) // 1024)
                    # Crash forensics heartbeat: alive marker in RTC memory
                    # (survives hard resets; 16-byte layout in boot.py)
                    try:
                        import machine, struct
                        raw = machine.RTC().memory()
                        cnt, lst, _, _ = struct.unpack("<IIII", raw[:16]) if len(raw) >= 16 else (0, 0, 0, 0)
                        machine.RTC().memory(struct.pack("<IIII", cnt, lst, 0x414C4956, int(time.time())))
                    except Exception:
                        pass

                # Flush any queued syslog log lines
                await logger.flush_syslog()

                # We can sleep longer here now since it isn't checking rapid knob turns
                await asyncio.sleep(1)

            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error(f"[MEM] telemetry loop error: {e}")
                await asyncio.sleep(1)

    def _log_memory_usage(self, reclaimed_kb=0):
        """Log current memory usage in KB (measured after the 30s GC tick)."""
        import gc
        logger = getattr(self, 'logger', log)
        try:
            free_kb = gc.mem_free() // 1024
            alloc_kb = gc.mem_alloc() // 1024
            total_kb = free_kb + alloc_kb

            # This integer math will now work better because the scale is smaller
            used_pct = (alloc_kb * 100) // total_kb if total_kb > 0 else 0

            self.state.set({MEMORY_USAGE: used_pct, CLIENT_ID: self.client_id or ""})
            logger.info(f"[MEM] Free: {free_kb} KB | Used: {used_pct}% ({alloc_kb} KB allocated) | GC reclaimed: {reclaimed_kb} KB")
            if free_kb < 2000:
                logger.warn(f"[MEM] LOW MEMORY: {free_kb} KB free — OOM risk")
        except Exception as e:
            logger.error(f"[MEM] error reading memory: {e}")

    async def _wdt_feed_loop(self):
        """Feed the hardware watchdog. Total event-loop freezes longer than the
        WDT timeout trip the reset; single-task hangs do not (this task keeps
        yielding as long as the loop itself runs)."""
        while True:
            self._wdt.feed()
            await asyncio.sleep_ms(3000)

async def main():
    """Entry point for async app."""
    app = JukeBoxApp()
    await app.run()


if __name__ == "__main__":
    log.info(f"[BOOT] starting Jukebox app...")
    import asyncio
    asyncio.run(main())
