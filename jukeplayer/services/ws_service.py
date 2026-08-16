from jukeplayer.core.state_constants import *
from jukeplayer.core.ws_events import AsyncWebsocketClient

class WSService:
    def __init__(self, app):
        self.app = app

    async def message_handler(self, data):
        import json, gc

        try:

            msg = json.loads(data)
            msg_type = msg.get("type")
            payload = msg.get("payload", {})

            handler_name = f"handle_{msg_type}"
            handler = getattr(self, handler_name, None) #self.handlers.get(msg_type)

            # self.app.logger.info(f"Received message of type: {msg_type} using handler: {handler.__name__ if handler else 'None'} with payload: {payload}")
        
            if handler:
                await handler(payload)
            else:
                self.app.logger.info(f"Unknown message type: {msg_type}")

        except Exception as e:
            self.app.logger.error(f"Error in message_handler: {e}")
        finally:
            msg = None
            gc.collect() 

    async def handle_ping(self, payload):
        pass

    async def handle_register_response(self, payload):
        # In case we receive a register_response after initial registration
        if payload.get("status") == "success":
            self.app.client_id = payload.get("client_id")
            self.app.state.set(
                {
                    CLIENT_ID: self.app.client_id or "",
                    NETWORK_STATUS: "WS:OK",
                    WS_CONNECTED: True,
                }
            )
            self.app.logger.info(f"✅ Client ID updated: {self.app.client_id}")

    async def handle_current_track(self, payload):

        track_data = payload.get("current_track", {})
        repeat_status = payload.get("repeat_album", False)
        muted = payload.get("muted", False)
        volume = payload.get("volume", 0)
        playlist_count = payload.get("playlist_count", 0)
        status = payload.get("status", "")

        title = track_data.get("title", "")
        track_number = track_data.get("track_number", 0)
        album = track_data.get("album", "")
        artist = track_data.get("artist", "")
        cover_url = track_data.get("cover_url", None)
        year = track_data.get("year", "")

        # Apply initial volume if present
        self.app.logger.info(f"Initial volume: {payload['volume']}")
        self.app.encoder.set(value=volume)
        
        self.app.logger.info(f"Track update - {artist} / {title} / {year} / {track_number} / {playlist_count} / {repeat_status} / {muted} (status: {status}, volume: {volume})")
        
            
        if status == "playing":
            status = "PLAY"
        elif status == "paused":
            status = "PAUSE"
        elif status in ["stopped", "idle"]:
            status = "STOP"

        self.app.state.set(
            {
                VOLUME: volume,
                REPEAT_STATUS: repeat_status,
                TITLE: title,
                TRACK: title,
                TRACK_NUMBER: track_number,
                PLAYLIST_COUNT: playlist_count,
                YEAR: year,
                ARTIST: artist,
                ALBUM: album,
                PLAYER_STATUS: status,
                COVER_URL: cover_url,
                MUTED: muted

            }
        )

    async def handle_volume_changed(self, payload):
        self.app.logger.info(f"Volume update received: {payload}")
        if isinstance(payload, dict):
            volume = payload.get("volume", payload.get("value", 0))
            mute_status = payload.get("muted")
        else:
            volume = payload
            mute_status = None
        
        self.app.logger.info(f"Volume update - {volume}")
        
        self.app.state.set({VOLUME: volume})
        if mute_status is not None:
            self.app.state.set({MUTED: bool(mute_status)})

    async def handle_notification(self, payload):
        pass

    async def handle_toggle_repeat_changed(self, payload):
        repeat_status = payload.get('mode')
        self.app.state.set({REPEAT_STATUS: repeat_status})
        self.app.logger.info(f"Repeat status update - {repeat_status} / {payload}")                

    # async def handle_mute_changed(self, payload):
    #     muted = payload.get("muted") if isinstance(payload, dict) else payload
    #     self.app.state.set({"mute_status": bool(muted)})
    #     self.app.logger.info(f"Mute status update - {self.app.state.get('mute_status')}")

    async def handle_volume_muted(self, payload):
        muted = payload.get("muted") if isinstance(payload, dict) else payload
        self.app.state.set({MUTED: bool(muted)})
        self.app.logger.info(f"Mute status update - {self.app.state.get(MUTED)}")
        
        # await self.handle_mute_changed(payload)

    async def handle_error(self, payload):
        pass

    async def handle_nfc_encode_start(self, payload):
        # Receive NFC encoding command from backend
        album_id = payload.get("album_id")
        self.app.logger.info(f"NFC encode ready to write album_id: {album_id} - waiting for microswitch press to start encoding")
        self.app.state.set({NFC_ENCODING_ALBUM_ID: album_id})
        self.app.state.set({NFC_WRITE_STATE: True})
        # now wait for the microswitch press to trigger the actual write in handle_microswitch_press, which will write to the card

    async def handle_nfc_encode_completed(self, payload):
        pass

    async def register_with_backend(self):
        """Send registration message to backend."""
        import time, asyncio, json

        try:
            registration_msg = {
                "type": "register_client",
                "payload": {
                    "client_type": self.app.config["client"]["type"],
                    "client_name": self.app.config["client"]["name"],
                    "capabilities": self.app.config["capabilities"],
                    "device_id": self.app.config["client"].get("device_id")
                }
            }
            
            # Send registration as JSON string
            reg_json = json.dumps(registration_msg)
            await self.app.ws.send(reg_json)
            self.app.logger.info(f"📝 Registration message sent: {self.app.config['client']['name']} to backend {self.app.config['backend']['ip']}:{self.app.config['backend']['port']}")
            
            timeout_end = time.time() + 5
            while time.time() < timeout_end:
                response_text = await asyncio.wait_for(
                    self.app.ws.recv(), 
                    timeout=max(0.1, timeout_end - time.time())
                )
                if response_text:
                    response_data = json.loads(response_text)
                    if response_data.get("type") == "register_response":
                        payload = response_data.get("payload", {})
                        if payload.get("status") == "success":
                            self.app.client_id = payload.get("client_id")
                            self.app.state.set(
                                {
                                    CLIENT_ID: self.app.client_id or "",
                                    NETWORK_STATUS: "WS:OK",
                                    WS_CONNECTED: True,
                                }
                            )
                            self.app.logger.info(f"✅ Registration successful: {self.app.config['client']['name']} (ID: {self.app.client_id})")
                            # self.app.state.set({"network_status": "WS:OK"})
                            return
                        else:
                            self.app.logger.info(f"❌ Registration failed: {payload.get('message')}")
                            return
                    else:
                        # Process other message types (e.g., current_track) during registration
                        await self.app.ws_service.message_handler(response_text)
            
            self.app.logger.info(f"❌ Registration response timeout")
        
        except asyncio.TimeoutError:
            self.app.logger.info(f"❌ Registration response timeout")
        except Exception as e:
            self.app.logger.info(f"❌ Registration error: {e}")  

    async def connect_websocket(self):
        """Connect to WebSocket server with retries and register client."""
        max_attempts = 5
        import network, asyncio, gc
        wlan = network.WLAN(network.STA_IF)
        
        for attempt in range(max_attempts):
            gc.collect()  # Prevent memory leak during long reconnect phases
            self.app.state.set({WS_CONNECTED: False})
            
            # Check WiFi first before creating sockets or doing getaddrinfo to prevent LWIP ENOMEM leaks!
            if not wlan.isconnected():
                self.app.logger.info(f"[CONNECT] WiFi is down! Waiting before reconnect attempt...")
                self.app.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})
                await asyncio.sleep(5)
                continue
                
            try:
                # Recreate client if previous loop cleared it
                if self.app.ws is None:
                    self.app.ws = AsyncWebsocketClient(5)

                free_before = gc.mem_free()
                self.app.logger.info(f"[CONNECT] Attempting connection (attempt {attempt + 1}/{max_attempts})")
                self.app.logger.info(f"[CONNECT] Heap before handshake: {free_before} bytes free")
                # Add timeout so handshake doesn't hang forever
                connected = await asyncio.wait_for(self.app.ws.handshake(self.app.server_url), timeout=5)
                if connected:
                    self.app.logger.info(f"[CONNECT] WebSocket connected successfully")
                    self.app.state.set({WS_CONNECTED: True, NETWORK_STATUS: "WS:OK"})
                    # Send registration message immediately after connection
                    # await self.app.ws_service.register_with_backend()
                    await self.register_with_backend()
                    return
            except asyncio.TimeoutError:
                self.app.logger.info(f"[CONNECT] Attempt {attempt + 1} timed out (5s)")
            except Exception as e:
                self.app.logger.info(f"[CONNECT] Attempt {attempt + 1} failed: {e}")

            # Ensure partial sockets/streams are torn down before retry.
            try:
                if self.app.ws:
                    await self.app.ws.close()
            except Exception:
                pass

            self.app.ws = AsyncWebsocketClient(5)
            gc.collect()
            self.app.logger.info(f"[CONNECT] Heap after cleanup: {gc.mem_free()} bytes free")
            await asyncio.sleep(1)
        
        self.app.logger.info(f"[CONNECT] Failed to connect after retries")            
        self.app.state.set({NETWORK_STATUS: "WS:ERR", WS_CONNECTED: False})