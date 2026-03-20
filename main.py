import network
import time
import gc
import st7735
import asyncio
import json
from machine import Pin, SPI
from lib.display import Display
from lib.nfc_reader import NFCReader
from lib.api_client import MediaPlayerAPI
from lib.buttons import ButtonController
from ws import AsyncWebsocketClient

class JukeBoxApp:
    """Main application orchestrating display, NFC, API, and WebSocket."""
    
    def __init__(self):
        print("LOG: Initializing Hardware...")
        
        # SPI1/HSPI for NFC (GPIO 18/23/19)
        self.spi_nfc = SPI(1, baudrate=4000000, polarity=0, phase=0,
                           sck=Pin(18), mosi=Pin(23), miso=Pin(19))
        
        # SPI2/VSPI for Display (GPIO 14/13/12)
        self.spi_display = SPI(2, baudrate=4000000, polarity=0, phase=0,
                               sck=Pin(14), mosi=Pin(13), miso=Pin(12))
        
        # Initialize components with separate SPI buses
        self.display = Display(self.spi_display, dc_pin=2, reset_pin=12, cs_pin=15)
        self.nfc = NFCReader(self.spi_nfc, rst_pin=4, cs_pin=5)
        self.api = MediaPlayerAPI(host="192.168.68.102", port=8001)
        self.buttons = ButtonController()
        
        # WebSocket client (library-based)
        self.ws = AsyncWebsocketClient(5)  # socket_delay_ms as positional argument
        self.server_url = "ws://192.168.68.102:8001/ws/mediaplayer/status-minimal"
        
        # Polling configuration (fallback if WebSocket disconnects)
        self.last_rest_poll = 0
        self.rest_poll_interval = 120000  # 2 minutes - only as fallback
        
        # NFC polling rate limiting (don't check too frequently)
        self.last_nfc_check = 0
        self.nfc_check_interval = 200  # Check NFC every 200ms max

    async def run(self):
        """Main application loop using async pattern."""
        # Connect to WiFi with timeout
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        
        # TODO: Move WiFi credentials to config.json
        SSID = "neptunium"
        PASSWORD = "neppetun#1733"
        
        print(f"LOG: Connecting to WiFi: {SSID}")
        wlan.connect(SSID, PASSWORD)
        
        # Wait for connection with timeout (max 30 seconds)
        timeout = 30
        start_time = time.time()
        while not wlan.isconnected():
            if time.time() - start_time > timeout:
                print("LOG: ERROR - WiFi connection timeout after 30s")
                self.display.show_status("ERROR", "WiFi Timeout", st7735.TFT.RED)
                return
            await asyncio.sleep_ms(100)
        
        print(f"LOG: Connected IP: {wlan.ifconfig()[0]}")
        self.display.show_status("WiFi Connected!", wlan.ifconfig()[0], st7735.TFT.GREEN)
        self.display.set_connection_status("wifi_only")  # WiFi connected but no WS yet
        await asyncio.sleep(1.5)  # Brief feedback before proceeding
        self.display.show_status("READY", "Connecting WS...", st7735.TFT.GREEN)
        
        # Initial REST poll before connecting to WebSocket
        track_info = self.api.get_status()
        if track_info:
            self.display.update_track(track_info)
            print(f"LOG: Initial status loaded - volume={track_info.get('volume')}")
        
        # Main event loop - run WebSocket, input handling, and polling concurrently
        await asyncio.gather(
            self._websocket_loop(),
            self._nfc_loop(),
            self._polling_loop()
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
                        data = await self.ws.recv()
                        if data:
                            await self._handle_websocket_message(data)
                        
                        # Periodic garbage collection to prevent memory issues on ESP32
                        gc.collect()
                        await asyncio.sleep_ms(10)
                    except Exception as e:
                        print(f"LOG: WebSocket recv error: {e}")
                        break
                
                # Connection closed, prepare to reconnect
                print("LOG: [RECONNECT] WebSocket connection lost")
                self.display.set_connection_status("wifi_only")  # Back to WiFi only
                
            except KeyboardInterrupt:
                # Allow keyboard interrupt to propagate
                raise
            except Exception as e:
                print(f"LOG: WebSocket error: {e}")
            
            # Exponential backoff on reconnect (2s → 4s → 8s → ... → 30s)
            print(f"LOG: Reconnecting in {reconnect_delay}s...")
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
            
            if msg_type == "current_track":
                # Payload has nested structure: {"current_track": {...}, "status": "...", "volume": ...}
                track_data = payload.get("current_track", {})
                title = track_data.get("title", "")
                print(f"LOG: Track update - {title}")
                # Show brief status before updating display
                if title:
                    self.display.show_status("Now Playing", title[:30], st7735.TFT.GREEN)
                    await asyncio.sleep(0.8)  # Brief feedback
                # Create track_info dict matching API response format
                track_info = {
                    "title": track_data.get("title", ""),
                    "album": track_data.get("album", ""),
                    "artist": track_data.get("artist", ""),
                    "status": payload.get("status", ""),
                    "volume": payload.get("volume", 0)
                }
                self.display.update_track(track_info)
            elif msg_type == "volume_changed":
                volume = payload.get("volume", 0)
                self.display.show_status("Volume", f"{volume}%", st7735.TFT.YELLOW)
                await asyncio.sleep(0.5)  # Brief feedback
                self.display.update_volume(volume)
                print(f"LOG: Volume update - {volume}")
            elif msg_type == "ping":
                pass
                #print(f"LOG: [PING] Received heartbeat from server")
            elif msg_type == "notification":
                print(f"LOG: Notification - {payload.get('message')}")
            elif msg_type == "error":
                print(f"LOG: Server error - {payload.get('message')}")
        except Exception as e:
            print(f"LOG: Error parsing WebSocket message: {e}")

    async def _connect_websocket(self):
        """Connect to WebSocket server with retries."""
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                print(f"LOG: [CONNECT] Attempting connection (attempt {attempt + 1}/{max_attempts})")
                if await self.ws.handshake(self.server_url):
                    print(f"LOG: [CONNECT] WebSocket connected successfully")
                    self.display.show_status("Connected!", "WS Ready", st7735.TFT.GREEN)
                    self.display.set_connection_status("websocket")  # Full connection
                    await asyncio.sleep(1)  # Brief feedback
                    return
            except Exception as e:
                print(f"LOG: [CONNECT] Attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(1)
        
        print("LOG: [CONNECT] Failed to connect after retries")

    async def _nfc_loop(self):
        """Handle button presses and NFC card scanning."""
        while True:
            try:
                # Check for button presses (non-blocking)
                button = self.buttons.check_buttons()
                if button:
                    await self._handle_button_press(button)
                
                # Check for NFC card scan (rate limited to 200ms)
                now = time.ticks_ms()
                if time.ticks_diff(now, self.last_nfc_check) >= self.nfc_check_interval:
                    self.last_nfc_check = now
                    await self._check_nfc_card()
                
                # Periodic garbage collection
                gc.collect()
                # Yield to event loop frequently to prevent watchdog timeout
                await asyncio.sleep_ms(50)
                
            except KeyboardInterrupt:
                # Allow keyboard interrupt to propagate
                raise
            except Exception as e:
                print(f"LOG: Button/NFC loop error: {e}")
                await asyncio.sleep(1)

    async def _check_nfc_card(self):
        """Check for NFC card and handle scan if detected."""
        try:
            # NFC read is blocking, so we wrap it with timeout handling
            # The NFC reader has built-in timeout protection now
            album_id = self.nfc.read_album_id()
            if album_id:
                self.display.show_status("Card Detected!", "Reading...", st7735.TFT.CYAN)
                await asyncio.sleep(0.6)  # Brief feedback
                await self._handle_card_scanned(album_id)
                await asyncio.sleep(2)  # Debounce after successful read
        except KeyboardInterrupt:
            # Allow keyboard interrupt to propagate
            raise
        except Exception as e:
            print(f"LOG: NFC read error: {e}")

    async def _polling_loop(self):
        """Fallback REST polling when WebSocket is disconnected.
        
        Polls for track updates every 2 minutes if WebSocket is not connected,
        ensuring display stays updated even when WebSocket is down.
        """
        while True:
            try:
                now = time.ticks_ms()
                if time.ticks_diff(now, self.last_rest_poll) >= self.rest_poll_interval:
                    self.last_rest_poll = now
                    await self._try_rest_poll()
                
                # Check frequently but not excessively
                await asyncio.sleep_ms(100)
                
            except KeyboardInterrupt:
                # Allow keyboard interrupt to propagate
                raise
            except Exception as e:
                print(f"LOG: Polling loop error: {e}")
                await asyncio.sleep(1)

    async def _try_rest_poll(self):
        """Attempt REST poll if WebSocket is not connected."""
        if not await self.ws.open():
            print("LOG: WebSocket not connected, doing REST poll...")
            try:
                track_info = self.api.get_status()
                if track_info:
                    self.display.update_track(track_info)
                    print(f"LOG: REST poll successful - volume={track_info.get('volume')}")
            except Exception as e:
                print(f"LOG: REST poll failed: {e}")
        else:
            print("LOG: WebSocket connection checked and passed")

    async def _handle_button_press(self, button):
        """Handle button press by sending command to server."""
        print(f"LOG: Button pressed: {button}")
        
        success = False
        status_msg = ""
        
        if button == "play_pause":
            success = self.api.play_pause()
            status_msg = "Play/Pause"
        elif button == "next":
            success = self.api.next_track()
            status_msg = "Next Track"
        elif button == "prev":
            success = self.api.previous_track()
            status_msg = "Previous Track"
        elif button == "stop":
            success = self.api.stop()
            status_msg = "Stopped"
        
        if success:
            self.display.show_status(status_msg, "", st7735.TFT.CYAN)
            await asyncio.sleep(0.5)  # Brief feedback
            print(f"LOG: {button} command sent successfully")
        else:
            self.display.show_status(status_msg, "Failed", st7735.TFT.RED)
            await asyncio.sleep(1)  # Longer feedback for error
            print(f"LOG: {button} command failed")

    async def _handle_card_scanned(self, album_id):
        """Handle NFC card scan with album ID."""
        print(f"LOG: Card scanned with album ID: {album_id}")
        
        track_info = self.api.play_album(album_id)
        if track_info:
            # API returns normalized track info, pass directly to display
            self.display.update_track(track_info)
            print(f"LOG: Playing album - {track_info['title']}")
        else:
            print(f"LOG: Failed to play album {album_id}")


async def main():
    """Entry point for async app."""
    app = JukeBoxApp()
    await app.run()


if __name__ == "__main__":
    print("LOG: Starting Jukebox app...")
    asyncio.run(main())
