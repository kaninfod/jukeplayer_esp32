
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
        #self.app.logger.info("Received ping, sending pong")


    async def handle_register_response(self, payload):
        # In case we receive a register_response after initial registration
        if payload.get("status") == "success":
            self.app.client_id = payload.get("client_id")
            self.app.state["client_id"] = self.app.client_id or ""
            self.app.state["network_status"] = "ws_connected"
            self.app.logger.info(f"✅ Client ID updated: {self.app.client_id}")
            self.app.mqtt_service.publish_snapshot(reason="register_response")


    async def handle_current_track(self, payload):

        track_data = payload.get("current_track", {})
        repeat_status = payload.get("repeat_album", False)
        volume = payload.get("volume", 0)
        title = track_data.get("title", "")
        album = track_data.get("album", "")
        artist = track_data.get("artist", "")
        status = payload.get("status", "")
        
        # Apply initial volume if present
        self.app.logger.info(f"Initial volume: {payload['volume']}")
        self.app.encoder.set(value=volume)
        
        self.app.logger.info(f"Track update - {artist} / {title}  (status: {status}, volume: {volume})")
        
        # Update OLED Scroller if available
        
        self.app.oled.set_volume(volume)
        self.app.oled.set_repeat_status(repeat_status)
            
        if status == "playing":
            self.app.oled.set_player_status("PLAY")
            self.app.oled.set_artist(artist)
            display_text = title if title else "Unknown"
            self.app.oled.set_text(display_text)
        elif status == "paused":
            self.app.oled.set_player_status("PAUSE")
            self.app.oled.set_artist("")
            self.app.oled.set_text("Paused")
        elif status in ["stopped", "idle"]:
            self.app.oled.set_player_status("STOP")
            self.app.oled.set_artist("")
            self.app.oled.set_text("Idle")

        # Update local app state
        self.app.state["volume"] = volume
        self.app.state["repeat"] = repeat_status
        self.app.state["repeat_status"] = repeat_status
        self.app.state["title"] = title
        self.app.state["track"] = title
        self.app.state["artist"] = artist
        self.app.state["album"] = album
        self.app.state["status"] = status
        self.app.state["player_status"] = status

        # Publish state change immediately via MQTT
        self.app.mqtt_service.publish_snapshot(reason="current_track")

    async def handle_volume_changed(self, payload):
        self.app.logger.info(f"Volume update received: {payload}")
        if isinstance(payload, dict):
            volume = payload.get("volume", payload.get("value", 0))
            mute_status = payload.get("muted")
        else:
            volume = payload
            mute_status = None
        
        self.app.oled.set_volume(volume)
        self.app.logger.info(f"Volume update - {volume}")
        
        self.app.state["volume"] = volume
        if mute_status is not None:
            self.app.state["mute_status"] = bool(mute_status)
        self.app.mqtt_service.publish_snapshot(reason="volume_changed")

    async def handle_notification(self, payload):
        #self.app.logger.info(f"Notification - {payload.get('message')}")
        pass

    async def handle_toggle_repeat_changed(self, payload):
        repeat_status = payload.get('mode')
        
        self.app.oled.set_repeat_status(repeat_status)
        self.app.logger.info(f"Repeat status update - {repeat_status} / {payload}")                

        self.app.state["repeat"] = repeat_status
        self.app.state["repeat_status"] = repeat_status
        self.app.mqtt_service.publish_snapshot(reason="repeat_changed")

    async def handle_mute_changed(self, payload):
        muted = payload.get("muted") if isinstance(payload, dict) else payload
        self.app.state["mute_status"] = bool(muted)
        self.app.logger.info(f"Mute status update - {self.app.state['mute_status']}")
        self.app.mqtt_service.publish_snapshot(reason="mute_changed")

    async def handle_volume_muted(self, payload):
        await self.handle_mute_changed(payload)

    async def handle_error(self, payload):
        #self.app.logger.info(f"Server error - {payload.get('message')}")
        pass

    async def handle_nfc_encode_start(self, payload):
        # Receive NFC encoding command from backend
        album_id = payload.get("album_id")
        
        self.app.logger.info(f"NFC encode request for album_id: {album_id}")
        
        # Set encoding mode flag BEFORE creating task (prevents normal reads)
        # self.app.nfc_encoding_album_id = album_id
        
        self.app.state["nfc_encoding_album_id"] = album_id
        self.app.oled.set_text("Insert card to encode...")
        self.app.logger.info(f"NFC encode ready - waiting for microswitch press to start encoding")
        
        # now wait for the microswitch press to trigger the actual write in handle_microswitch_press, which will write to the card

    async def handle_nfc_encode_completed(self, payload):
        self.app.oled.set_text("Done encoding!")


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
                            self.app.state["client_id"] = self.app.client_id or ""
                            self.app.logger.info(f"✅ Registration successful: {self.app.config['client']['name']} (ID: {self.app.client_id})")
                            
                            self.app.oled.set_net_status("WS:OK")
                            self.app.state["network_status"] = "ws_connected"
                            self.app.mqtt_service.publish_snapshot(reason="register_success")
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
            
            # Check WiFi first before creating sockets or doing getaddrinfo to prevent LWIP ENOMEM leaks!
            if not wlan.isconnected():
                self.app.logger.info(f"[CONNECT] WiFi is down! Waiting before reconnect attempt...")
                self.app.state["network_status"] = "wifi_disconnected"
                self.app.mqtt_service.publish_snapshot(reason="wifi_disconnected")
                await asyncio.sleep(5)
                continue
                
            try:
                self.app.logger.info(f"[CONNECT] Attempting connection (attempt {attempt + 1}/{max_attempts})")
                # Add timeout so handshake doesn't hang forever
                connected = await asyncio.wait_for(self.app.ws.handshake(self.app.server_url), timeout=5)
                if connected:
                    self.app.logger.info(f"[CONNECT] WebSocket connected successfully")
                    # Send registration message immediately after connection
                    await self.app.ws_service.register_with_backend()
                    return
            except asyncio.TimeoutError:
                self.app.logger.info(f"[CONNECT] Attempt {attempt + 1} timed out (5s)")
            except Exception as e:
                self.app.logger.info(f"[CONNECT] Attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(1)
        
        self.app.logger.info(f"[CONNECT] Failed to connect after retries")            