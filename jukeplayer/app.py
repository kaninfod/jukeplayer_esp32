import network
import time
import gc
import asyncio
import json
from machine import Pin, SPI
# from jukeplayer.lib.display import Display  # TODO: Replace with 0.96" OLED 128x64 display
from jukeplayer.lib.nfc_reader import NFCReader
from jukeplayer.lib.input_controller import InputController
from jukeplayer.lib.ky_040 import KY040Controller
from jukeplayer.lib.ws_events import AsyncWebsocketClient
from jukeplayer.lib.http_audio_stream import HttpAudioStreamHandler
from jukeplayer.gui.oled_manager import OLEDScroller

__version__ = "FILESYSTEM_v1"  # Change to "FILESYSTEM_v1" in your device copy


from jukeplayer.lib.logger import log

def load_config():
    """Load configuration from config.json file at device root.
    
    NOTE: config.json is NOT frozen with the application, so it can be
    modified on the device without rebuilding the frozen firmware.
    The file must be located at the root of the device filesystem.
    """
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
        nfc_cfg = self.config.get("nfc_reader", {})
        vs1053_cfg = self.config.get("vs1053", {})
        encoder_cfg = self.config.get("encoder", {})
        buttons_cfg = self.config.get("buttons", {})
        input_cfg = self.config.get("input", {})
        
        # Initialize OLED Scroller (which sets up I2C internally based on config.oled)
        log.info("Initializing OLED Scroller...")
        try:
            self.oled = OLEDScroller(display_cfg)
            self.oled.start()
            self.oled.set_text("Jukeplayer Wait...")
            self.oled.set_net_status("WS:CON")
            self.oled.set_player_status("BOOT")
        except Exception as e:
            log.error(f"Failed to init OLED: {e}")
            self.oled = None
        
        # SPI1/HSPI for NFC
        self.spi_nfc = SPI(nfc_cfg.get("spi_unit", 1), 
                           baudrate=nfc_cfg.get("baudrate", 4000000), 
                           polarity=nfc_cfg.get("polarity", 0), 
                           phase=nfc_cfg.get("phase", 0),
                           sck=Pin(nfc_cfg.get("sck", 18)), 
                           mosi=Pin(nfc_cfg.get("mosi", 23)), 
                           miso=Pin(nfc_cfg.get("miso", 19)))
        
        try:
            self.nfc = NFCReader(self.spi_nfc, 
                                rst_pin=nfc_cfg.get("reset", 4), 
                                cs_pin=nfc_cfg.get("cs", 5), 
                                dummy_mode=self.dummy_mode)
        except Exception as e:
            log.info(f"WARNING - NFC reader initialization failed: {e}")
            self.nfc = None
        
        self.inputs = InputController(
            button_pins={
                'play_pause': buttons_cfg.get('play_pause', 21),
                'next': buttons_cfg.get('next', 22),
                'prev': buttons_cfg.get('previous', 25),
                'stop': buttons_cfg.get('stop', 26)
            },
            microswitch_pin=input_cfg.get('microswitch_nfc_card', 17),
            encoder_sw_pin=encoder_cfg.get('sw', 34),
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
        
        # VS1053 Hardware Initialization
        self.hardware_vs1053 = None
        if False and not self.dummy_mode and vs1053_cfg: # Disabled while testing on new board
            log.info("Initializing VS1053 Decoder...")
            try:
                # Requires Peter Hinch's micropython-vs1053 driver installed on ESP32
                from jukeplayer.lib.vs1053 import VS1053
                
                # VS1053 uses SPI bus hardware unit 2 (HSPI/VSPI)
                spi_vs = SPI(vs1053_cfg.get("spi_unit", 2),
                             baudrate=vs1053_cfg.get("baudrate", 2000000),
                             sck=Pin(vs1053_cfg.get("sck", 14)),
                             mosi=Pin(vs1053_cfg.get("mosi", 13)),
                             miso=Pin(vs1053_cfg.get("miso", 12)))
                
                reset_pin = None
                if vs1053_cfg.get("reset", -1) != -1:
                    reset_pin = Pin(vs1053_cfg["reset"], Pin.OUT)
                else:
                    # Provide a dummy callable for reset if not physically wired
                    reset_pin = lambda x: None
                
                self.hardware_vs1053 = VS1053(
                    spi_vs,
                    reset=reset_pin,
                    dreq=Pin(vs1053_cfg.get("dreq", 0), Pin.IN),
                    xdcs=Pin(vs1053_cfg.get("xdcs", 2), Pin.OUT),
                    xcs=Pin(vs1053_cfg.get("xcs", 15), Pin.OUT)
                )
                log.info("VS1053 Decoder initialized successfully!")
            except ImportError:
                log.info("WARNING - VS1053 driver (vs1053.py) not found. Falling back to stdout mock.")
            except Exception as e:
                log.info(f"WARNING - VS1053 hardware init failed: {e}")

        # Audio streaming handler (HTTP direct streaming proxying VS1053)
        # Pass the control WebSocket to send 'track_finished' when hardware indicates EOF.
        self.audio_handler = HttpAudioStreamHandler(self.config, control_ws=self.ws, hardware_vs1053=self.hardware_vs1053)
        
        # Memory monitoring (log every 30 seconds)
        self.last_memory_log = 0
        self.memory_log_interval = 30000  # Log memory every 30 seconds

    async def run(self):
        """Main application loop using async pattern."""
        
        # Wait briefly for backend and network to stabilize before WebSocket connection
        # This reduces failed handshake attempts on first boot
        log.info("Waiting 3 seconds before WebSocket connection...")
        await asyncio.sleep(3)
        
        import gc
        gc.collect()  # Free memory before API call
        
        # Main event loop - run WebSocket, audio stream, and input handling concurrently
        await asyncio.gather(
            self._websocket_loop(),
            self.audio_handler.stream_loop(),
            self._nfc_loop()
        )

    async def _websocket_loop(self):
        """Manage WebSocket connection and receive updates."""
        reconnect_delay = 2
        max_reconnect_delay = 30
        
        while True:
            try:
                await self._connect_websocket()
                reconnect_delay = 2  # Reset delay on successful connection
                
                # Read messages from server
                while await self.ws.open():
                    try:
                        gc.collect()  # Defragment heap before frame buffer allocation
                        data = await self.ws.recv()
                        if data:
                            await self._handle_websocket_message(data)
                        await asyncio.sleep_ms(10)
                    except Exception as e:
                        log.info(f"WebSocket recv error: {e}")
                        break
                
                # Connection closed, prepare to reconnect
                log.info(f"[RECONNECT] WebSocket connection lost")
                
            except KeyboardInterrupt:
                # Allow keyboard interrupt to propagate
                raise
            except Exception as e:
                log.info(f"WebSocket error: {e}")
            
            # Exponential backoff on reconnect (2s → 4s → 8s → ... → 30s)
            log.info(f"Reconnecting in {reconnect_delay}s...")
            if self.oled:
                self.oled.set_net_status("WS:ERR")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    async def _handle_websocket_message(self, data):
        """Process incoming WebSocket message.
        
        Args:
            data: Raw WebSocket data (JSON string)
        """
        try:
            msg = json.loads(data)
            msg_type = msg.get("type")
            payload = msg.get("payload", {})
            
            if msg_type == "register_response":
                # This is handled during registration, just ignore if received again
                log.info(f"Received registration response (already processed)")
            elif msg_type == "current_track":
                # Payload has nested structure: {"current_track": {...}, "status": "...", "volume": ...}
                track_data = payload.get("current_track", {})
                title = track_data.get("title")
                status = payload.get("status", "")
                target_backend = payload.get("playback_backend")
                active_client = payload.get("active_client")
                
                # Apply initial volume if present
                if "volume" in payload:
                    self._apply_hardware_volume(payload["volume"])
                
                log.info(f"Track update - {title} (status: {status}, backend: {target_backend}, target_id: {active_client})")
                
                # Update audio handler state based on playback status
                # IMPORTANT: Must handle ALL possible status values to avoid state drift
                if status == "playing" and track_data:
                    # Valid play state: have a track and asked to play
                    if target_backend == "chromecast":
                        log.info("Ignoring audio stream - playback_backend is chromecast")
                        self.audio_handler.set_state("idle")
                    elif active_client is not None and active_client != self.client_id:
                        log.info(f"Ignoring audio stream - we are not the active client (target: {active_client}, self: {self.client_id})")
                        self.audio_handler.set_state("idle")
                    else:
                        self.audio_handler.set_state("play")
                elif status == "playing" and not track_data:
                    # Backend says playing but no track - treat as stopped
                    log.info(f"Track update - ignoring play state with no track")
                    self.audio_handler.set_state("idle")
                elif status == "paused":
                    self.audio_handler.set_state("paused")
                elif status == "stopped" or status == "idle":
                    # Both "stopped" and "idle" mean: stop audio stream
                    self.audio_handler.set_state("idle")
                else:
                    # Unknown status - default to idle to be safe
                    log.info(f"Track update - unknown status '{status}', defaulting to idle")
                    self.audio_handler.set_state("idle")
                
                # Create track_info dict matching API response format
                track_info = {
                    "title": track_data.get("title", ""),
                    "album": track_data.get("album", ""),
                    "artist": track_data.get("artist", ""),
                    "status": payload.get("status", ""),
                    "volume": payload.get("volume", 0)
                }
                
                # Update OLED Scroller if available
                if self.oled and title:
                    artist = track_info.get("artist", "Unknown")
                    album = track_info.get("album", "")
                    
                    if status == "playing":
                        self.oled.set_player_status("PLAY")
                        display_text = f"{title} - {artist}"
                        if album:
                            display_text += f" ({album})"
                        self.oled.set_text(display_text)
                    elif status == "paused":
                        self.oled.set_player_status("PAUSE")
                        self.oled.set_text("Paused")
                    elif status in ["stopped", "idle"]:
                        self.oled.set_player_status("STOP")
                        self.oled.set_text("Idle")
                
                # self.display.update_track(track_info)  # TODO: Remove old display logic
            elif msg_type == "volume_changed":
                volume = payload.get("volume", 0)
                # self.display.update_volume(volume)  # TODO: Remove old display logic
                log.info(f"Volume update - {volume}")
                self._apply_hardware_volume(volume)
            elif msg_type == "ping":
                pass
            elif msg_type == "notification":
                log.info(f"Notification - {payload.get('message')}")
            elif msg_type == "error":
                log.info(f"Server error - {payload.get('message')}")
            elif msg_type == "encode_nfc":
                # Receive NFC encoding command from backend
                album_id = payload.get("album_id")
                log.info(f"NFC encode request for album_id: {album_id}")
                # Set encoding mode flag BEFORE creating task (prevents normal reads)
                self.nfc_encoding_album_id = album_id
                # Fire and forget - run encoding in background to keep WebSocket responsive
                asyncio.create_task(self._send_nfc_result(album_id, "NFC-BG"))
        except Exception as e:
            log.info(f"Error parsing WebSocket message: {e}")

    async def _send_nfc_result(self, album_id, context=""):
        """Unified NFC write handler - send result to backend.
        
        Args:
            album_id: Album ID to write to card
            context: Log context prefix (e.g., 'NFC-BG', 'MS-WRITE')
        """
        if self.nfc is None:
            if context:
                log.info(f"[{context}] NFC reader not available - skipping write")
            return
        
        try:
            if context:
                log.info(f"[{context}] Starting write_data for album_id: {album_id}")
            
            result = self.nfc.write_data(album_id, timeout_ms=30000)
            
            if context:
                log.info(f"[{context}] Result - status: {result.get('status')}, uid: {result.get('uid')}")
            
            # Send completion message back to backend
            response = {
                "type": "nfc_encoding_complete",
                "payload": result
            }
            await self.ws.send(json.dumps(response))
            
            if context:
                log.info(f"[{context}] Sent encoding result to backend")
        except Exception as e:
            if context:
                log.info(f"[{context}] Error: {e}")
            
            response = {
                "type": "nfc_encoding_complete",
                "payload": {
                    "status": "error",
                    "uid": None,
                    "error_message": str(e)
                }
            }
            try:
                await self.ws.send(json.dumps(response))
            except Exception as send_error:
                if context:
                    log.info(f"[{context}] Failed to send error: {send_error}")
        finally:
            # Clear encoding flag
            if self.nfc_encoding_album_id == album_id:
                self.nfc_encoding_album_id = None
                if context:
                    log.info(f"[{context}] Encoding mode cleared")

    async def _connect_websocket(self):
        """Connect to WebSocket server with retries and register client."""
        max_attempts = 5
        for attempt in range(max_attempts):
            gc.collect()  # Prevent memory leak during long reconnect phases
            try:
                log.info(f"[CONNECT] Attempting connection (attempt {attempt + 1}/{max_attempts})")
                # Add timeout so handshake doesn't hang forever
                connected = await asyncio.wait_for(self.ws.handshake(self.server_url), timeout=5)
                if connected:
                    log.info(f"[CONNECT] WebSocket connected successfully")
                    # Send registration message immediately after connection
                    await self._register_with_backend()
                    return
            except asyncio.TimeoutError:
                log.info(f"[CONNECT] Attempt {attempt + 1} timed out (5s)")
            except Exception as e:
                log.info(f"[CONNECT] Attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(1)
        
        log.info(f"[CONNECT] Failed to connect after retries")
    
    async def _register_with_backend(self):
        """Send registration message to backend."""
        try:
            registration_msg = {
                "type": "register_client",
                "payload": {
                    "client_type": self.config["client"]["type"],
                    "client_name": self.config["client"]["name"],
                    "capabilities": self.config["capabilities"]
                }
            }
            
            # Send registration as JSON string
            reg_json = json.dumps(registration_msg)
            await self.ws.send(reg_json)
            log.info(f"📝 Registration message sent: {self.config['client']['name']}")
            
            # Wait for registration response (may receive other messages first)
            # Keep receiving until we get the register_response
            timeout_end = time.time() + 5
            while time.time() < timeout_end:
                response_text = await asyncio.wait_for(
                    self.ws.recv(), 
                    timeout=max(0.1, timeout_end - time.time())
                )
                if response_text:
                    response_data = json.loads(response_text)
                    
                    if response_data.get("type") == "register_response":
                        payload = response_data.get("payload", {})
                        if payload.get("status") == "success":
                            self.client_id = payload.get("client_id")
                            log.info(f"✅ Registration successful: {self.config['client']['name']} (ID: {self.client_id})")
                            if self.oled:
                                self.oled.set_net_status("WS:OK")
                            # We don't want to overwrite the track info with "Connected" permanently.                                                                    
                            # It is better to just let the main WebSocket loop handle state.
                            return
                        else:
                            log.info(f"❌ Registration failed: {payload.get('message')}")
                            return
                    else:
                        # Process other message types (e.g., current_track) during registration
                        await self._handle_websocket_message(response_text)
            
            log.info(f"❌ Registration response timeout")
        
        except asyncio.TimeoutError:
            log.info(f"❌ Registration response timeout")
        except Exception as e:
            log.info(f"❌ Registration error: {e}")

    async def _nfc_loop(self):
        """Handle button presses, microswitch, rotary encoder, and potentiometer input."""
        while True:
            try:
                # Check all inputs (buttons and microswitch)
                input_name = self.inputs.check_inputs()
                if input_name:
                    if input_name == InputController.NFC_CARD:
                        await self._handle_microswitch_press()
                    else:
                        await self._handle_button_press(input_name)
                
                # Check if encoder volume changed (flag set by IRQ handler)
                if self.encoder.volume_changed:
                    # Update display immediately (local feedback)
                    current_volume = self.encoder.get_value()
                    # self.display.update_volume(current_volume)  # TODO: Update OLED display when available
                    log.info(f"Display updated to {current_volume}% (local encoder change)")
                    
                    # Check if debounce window has elapsed to send API call
                    if self.encoder.should_send_api_update(debounce_ms=300):
                        self.encoder.volume_changed = False  # Clear flag after sending
                        await self._handle_volume_change(current_volume)
                
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
                log.info(f"Button/NFC/Encoder/Pot loop error: {e}")
                await asyncio.sleep(1)

    async def _handle_microswitch_press(self):
        """Handle microswitch press - read card in normal mode, write in encoding mode."""
        if self.nfc is None:
            log.info(f"NFC reader not available")
            return
        
        log.info(f"Microswitch pressed")
        
        # Check if we're in NFC encoding mode
        if self.nfc_encoding_album_id:
            # ENCODING MODE: Write the album ID to the card
            album_id = self.nfc_encoding_album_id
            log.info(f"[MS] ENCODING MODE - Writing album_id: {album_id}")
            await self._send_nfc_result(album_id, "MS-WRITE")
        else:
            # NORMAL MODE: Read from the card
            log.info(f"[MS] NORMAL MODE - Reading card")
            await self._handle_microswitch_read()

    
    async def _handle_microswitch_read(self):
        """Read card via microswitch trigger."""
        if self.nfc is None:
            log.info(f"[MS-READ] NFC reader not available")
            return
        
        try:
            album_id = self.nfc.read_album_id()
            if album_id:
                log.info(f"[MS-READ] Card read - album_id: {album_id}")
                await self._handle_card_scanned(album_id)
                await asyncio.sleep(1)  # Debounce after successful read
            else:
                log.info(f"[MS-READ] No card detected or read failed")
        except Exception as e:
            log.info(f"[MS-READ] Error: {e}")
    
    async def _send_json(self, msg_dict, log_msg=None):
        """Wrapper to safely serialize and send JSON over WebSocket.
        Forces garbage collection before/after serializing to prevent memory fragmentation."""
        gc.collect()
        try:
            msg_str = json.dumps(msg_dict)
            await self.ws.send(msg_str)
            if log_msg:
                log.info(log_msg)
            return True
        except Exception as e:
            log.error(f"WebSocket send failed: {e}")
            return False
        finally:
            gc.collect()

    def _apply_hardware_volume(self, volume_percent):
        """Map 0-100% volume to VS1053 dB attenuation scale."""
        if not self.hardware_vs1053:
            return
            
        try:
            vol_int = int(volume_percent)
            if vol_int <= 0:
                self.hardware_vs1053.volume(0, 0, powerdown=True)
                log.info("Hardware volume: MUTE")
            else:
                # 100% -> 0 dB attenuation.
                # Peter Hinch driver expects passing negative values of attenuation, e.g. -6 for -6dB.
                # Let's map vol_int (1-100) to approx -60 to 0. 100 is 0. 50 is -30.
                attenuation_db = -60 + (vol_int * 0.6)
                # Do not exceed 0.
                attenuation_db = min(0, attenuation_db)
                self.hardware_vs1053.volume(attenuation_db, attenuation_db, powerdown=False)
                # log.info(f"Hardware volume set to {attenuation_db:.1f} dB")
        except Exception as e:
            log.info(f"Failed to set hardware volume: {e}")

    async def _handle_volume_change(self, volume):
        """Handle potentiometer volume change - send via WebSocket immediately."""
        try:
            log.info(f"Volume change - {volume}%")
            gc.collect()  # Free memory before sending
            
            # Send volume command via WebSocket
            msg = {
                "type": "volume",
                "payload": {
                    "value": volume
                }
            }
            await self.ws.send(json.dumps(msg))
            log.info(f"Volume {volume} sent to backend")
            gc.collect()  # Free memory after sending
        except Exception as e:
            log.info(f"Volume control error: {e}")


    async def _handle_card_scanned(self, album_id):
        """Handle NFC card scan with album ID."""
        log.info(f"Card scanned with album ID: {album_id}")
        
        gc.collect()  # Free memory before WS call
        msg = {
            "type": "play_album",
            "payload": {
                "album_id": album_id
            }
        }
        try:
            await self.ws.send(json.dumps(msg))
            log.info(f"Requested to play album_id={album_id}")
        except Exception as e:
            log.info(f"Failed to send play album command: {e}")
        gc.collect()  # Free memory after WS call

    async def _handle_button_press(self, button):
        """Handle button press by sending command via WebSocket.
        
        For play/pause/stop: immediately update audio state (don't wait for backend).
        For next/prev: immediately transition to idle state (track is changing).
        Then send control command via WebSocket /events endpoint.
        """
        log.info(f"Button pressed: {button}")
        
        gc.collect()  # Free memory before sending
        success = False
        
        if button == "play_pause" or button == "ky040_push":
            # Pause if currently in play state, play if paused
            if self.audio_handler.state == "play":
                self.audio_handler.set_state("paused")
                log.info(f"Audio paused immediately (awaiting backend confirmation)")
            elif self.audio_handler.state == "paused":
                self.audio_handler.set_state("play")
                log.info(f"Audio resumed immediately (awaiting backend confirmation)")
            
            # Send command via WebSocket
            msg = {
                "type": "play_pause",
                "payload": {}
            }
            try:
                await self.ws.send(json.dumps(msg))
                success = True
            except Exception as e:
                log.info(f"Failed to send play_pause command: {e}")
                success = False
        
        elif button == "next":
            # Track changing: set idle, backend will send new track with state
            self.audio_handler.set_state("idle")
            log.info(f"Transitioning to next track (audio stopped immediately)")
            
            msg = {
                "type": "next_track",
                "payload": {}
            }
            try:
                await self.ws.send(json.dumps(msg))
                success = True
            except Exception as e:
                log.info(f"Failed to send next_track command: {e}")
                success = False
        
        elif button == "prev":
            # Track changing: set idle, backend will send new track with state
            self.audio_handler.set_state("idle")
            log.info(f"Transitioning to previous track (audio stopped immediately)")
            
            msg = {
                "type": "previous_track",
                "payload": {}
            }
            try:
                await self.ws.send(json.dumps(msg))
                success = True
            except Exception as e:
                log.info(f"Failed to send previous_track command: {e}")
                success = False
        
        elif button == "stop":
            self.audio_handler.set_state("idle")
            log.info(f"Audio stopped immediately (awaiting backend confirmation)")
            
            msg = {
                "type": "stop",
                "payload": {}
            }
            try:
                await self.ws.send(json.dumps(msg))
                success = True
            except Exception as e:
                log.info(f"Failed to send stop command: {e}")
                success = False
        
        if success:
            log.info(f"{button} command sent successfully")
        else:
            log.info(f"{button} command failed")
        gc.collect()  # Free memory after sending

    def _log_memory_usage(self):
        """Log current memory usage (free and allocated)."""
        try:
            free = gc.mem_free()
            alloc = gc.mem_alloc()
            total = free + alloc
            used_pct = (alloc * 100) // total if total > 0 else 0
            log.info(f"[MEM] Free: {free} bytes | Alloc: {alloc} bytes | Total: {total} bytes | Used: {used_pct}%")
        except Exception as e:
            log.info(f"[MEM] Error reading memory: {e}")



async def main():
    """Entry point for async app."""
    app = JukeBoxApp()
    await app.run()


if __name__ == "__main__":
    log.info(f"Starting Jukebox app...")
    asyncio.run(main())
