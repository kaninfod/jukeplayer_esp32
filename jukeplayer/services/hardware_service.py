class HardwareService:
    
    def __init__(self, app):
        self.app = app

    async def handle_volume_change(self, volume):
        """Handle potentiometer volume change - send via WebSocket immediately."""
        import gc, json
        try:
            self.app.logger.info(f"Volume change - {volume}%")
            gc.collect()  # Free memory before sending
            
            # Send volume command via WebSocket
            msg = {
                "type": "volume",
                "payload": {
                    "value": volume
                }
            }
            await self.app.ws.send(json.dumps(msg))
            self.app.logger.info(f"Volume {volume} sent to backend")
            gc.collect()  # Free memory after sending
        except Exception as e:
            self.app.logger.info(f"Volume control error: {e}")

    async def handle_button_press(self, button_name, press_type="single"):
        """Handle button press by sending command via WebSocket.
        
        Send control command via WebSocket /events endpoint.
        """
        import gc, json
        self.app.logger.info(f"Button pressed: {button_name} with press type: {press_type}")
        
        gc.collect()  # Free memory before sending
        success = False
        
        # allowed commands from for ws action
        ws_command_whitelist = ["play_pause", "next_track", "previous_track", "stop"]
        
        if button_name in ws_command_whitelist and press_type == "single":
            try:
                await self.app.ws.send(json.dumps( { "type": button_name, "payload": {} } ))
                success = True
            except Exception as e:
                self.app.logger.info(f"Failed to send {button_name} command: {e}")
                success = False

        elif button_name == "nfc_card":
            await self.handle_microswitch_press()
            self.app.logger.info(f"NFC Microswitch pressed")
            success = True
        elif button_name == "encoder_press" and press_type == "single":
            self.app.state["menu_mode"] = True
            self.app.logger.info(f"Entering menu mode")
        elif button_name == "previous_track" and press_type == "long":
            self.app.state["menu_mode"] = False
            self.app.logger.info(f"Exiting menu mode")                   
        elif button_name == "stop" and press_type == "long":
            try:
                await self.app.ws.send(json.dumps( { "type": "volume_mute", "payload": {} } ))
                self.app.state["mute_status"] = not bool(self.app.state.get("mute_status", False))
                self.app.mqtt_service.publish_snapshot(reason="mute_toggle")
                self.app.logger.info(f"Volume mute command sent")
                success = True
            except Exception as e:
                self.app.logger.info(f"Failed to send {button_name} command: {e}")
                success = False


        if not success:
            self.app.logger.info(f"{button_name} command not supported for press type {press_type}")
        gc.collect()  # Free memory after sending            

    async def handle_card_scanned(self, album_id):
        """Handle NFC card scan with album ID."""
        import gc, json

        self.app.logger.info(f"Card scanned with album ID: {album_id}")
        
        gc.collect()  # Free memory before WS call
        msg = {
            "type": "play_album",
            "payload": {
                "album_id": album_id
            }
        }
        try:
            await self.app.ws.send(json.dumps(msg))
            self.app.logger.info(f"Requested to play album_id={album_id}")
        except Exception as e:
            self.app.logger.info(f"Failed to send play album command: {e}")
        gc.collect()  # Free memory after WS call
    
    async def handle_microswitch_press(self):
        """Handle microswitch press - read card in normal mode, write in encoding mode."""
        import json

        try:
            if self.app.nfc is None:
                self.app.logger.info(f"NFC reader not available")
                return
            
            self.app.logger.info(f"Microswitch pressed")
            
            # Check if we're in NFC encoding mode
            nfc_encoding_album_id = self.app.state.get("nfc_encoding_album_id") 
            self.app.logger.info(f" write_nfc_data called with album_id: {nfc_encoding_album_id}")
            if nfc_encoding_album_id:
                # ENCODING MODE: Write the album ID to the card
                
                response = {
                    "type": "nfc_encoding_started",
                    "payload": {"album_id": nfc_encoding_album_id}
                }
                await self.app.ws.send(json.dumps(response))

                self.app.logger.info(f"[MS] ENCODING MODE - Writing album_id: {nfc_encoding_album_id}")
                display_text = f"Encoding album_id: {nfc_encoding_album_id}"
                
                self.app.oled.set_text(display_text)
                await self.write_nfc_data(nfc_encoding_album_id, "MS-WRITE")
            else:
                # NORMAL MODE: Read from the card
                self.app.logger.info(f"[MS] NORMAL MODE - Reading card")
                self.app.oled.set_text("Reading card...")
                await self.handle_read_nfc()        
        except Exception as e:
            self.app.logger.info(f"Error handling microswitch press: {e}")

    async def write_nfc_data(self, album_id, context=""):
        """Unified NFC write handler - send result to backend.
        
        Args:
            album_id: Album ID to write to card
            context: Log context prefix (e.g., 'NFC-BG', 'MS-WRITE')
        """

        import json
        
        if self.app.nfc is None:
            if context:
                self.app.logger.info(f"[{context}] NFC reader not available - skipping write")
            return
        
        try:
            if context:
                self.app.logger.info(f"[{context}] Starting write_data for album_id: {album_id}")
            
            result = self.app.nfc.write_data(album_id, timeout_ms=30000)
            
            if context:
                self.app.logger.info(f"[{context}] Result - status: {result.get('status')}, uid: {result.get('uid')}")
            
            # Send completion message back to backend
            response = {
                "type": "nfc_encoding_complete",
                "payload": result
            }
            await self.app.ws.send(json.dumps(response))
            
            if context:
                self.app.logger.info(f"[{context}] Sent encoding result to backend")
        except Exception as e:
            if context:
                self.app.logger.info(f"[{context}] Error: {e}")
            
            response = {
                "type": "nfc_encoding_complete",
                "payload": {
                    "status": "error",
                    "uid": None,
                    "error_message": str(e)
                }
            }
            try:
                await self.app.ws.send(json.dumps(response))
            except Exception as send_error:
                if context:
                    self.app.logger.info(f"[{context}] Failed to send error: {send_error}")
        finally:
            # Clear encoding flag
            if self.app.state.get("nfc_encoding_album_id") == album_id:
                self.app.state["nfc_encoding_album_id"] = None
                if context:
                    self.app.logger.info(f"[{context}] Encoding mode cleared")


    async def handle_read_nfc(self):
        """Read card via microswitch trigger."""
        import asyncio

        if self.app.nfc is None:
            self.app.logger.info(f"[MS-READ] NFC reader not available")
            return
        
        try:
            album_id = self.app.nfc.read_album_id()
            if album_id:
                self.app.logger.info(f"[MS-READ] Card read - album_id: {album_id}")
                await self.handle_card_scanned(album_id)
                await asyncio.sleep(1)  # Debounce after successful read
            else:
                self.app.logger.info(f"[MS-READ] No card detected or read failed")
        except Exception as e:
            self.app.logger.info(f"[MS-READ] Error: {e}")

    async def set_volume_debounce_worker(self, current_volume, debounce_s=0.3):
        """Wait briefly, then send the latest volume update to backend."""
        import asyncio

        try:
            await asyncio.sleep(debounce_s)
            await self.handle_volume_change(current_volume)
        except asyncio.CancelledError:
            # Expected when a new knob turn supersedes the previous pending update.
            return
        except Exception as e:
            self.app.logger.info(f"Volume debounce worker error: {e}")

    def on_encoder_change(self):
        """Fires immediately via the hardware interrupt when the knob is turned."""
        
        import asyncio
        current_volume = self.app.encoder.value()
        self.app.oled.set_volume(current_volume)
        self.app.logger.info(f"Encoder turned - current volume: {current_volume}%")
        # Manage the 300ms debounce API timer
        if self.app.debounce_task and not self.app.debounce_task.done():
            self.app.debounce_task.cancel()
            
        # Schedule the worker directly onto the async event loop
        self.app.debounce_task = asyncio.create_task(
            self.set_volume_debounce_worker(current_volume)
        )   

    def make_callback(self,button_name, press_type):
        import asyncio
        asyncio.create_task(self.handle_button_press(button_name, press_type))

    def assign_button_handlers(self):
        if isinstance(self.app.pushbuttons, list):
            self.app.logger.info("Wiring up physical pushbutton callbacks...")

            for pb in self.app.pushbuttons:
                pb.on_press = self.make_callback
                self.app.logger.info(f"Button {pb.action_name} wired up")
        else:
            self.app.logger.info("Using dummy input controller; skipping physical callback wiring.")        