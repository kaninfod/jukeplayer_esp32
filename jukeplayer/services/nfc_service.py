from jukeplayer.core.state_constants import *

class NFCService: 
    def __init__(self, app):
        self.app = app

    async def handle_microswitch_press(self):
        """Handle microswitch press - read card in normal mode, write in encoding mode."""
        self.app.display.wake()  # the microswitch press is user presence
        import json

        try:
            if self.app.nfc is None:
                self.app.logger.warn(f"[NFC] reader not available")
                return
            
            self.app.logger.info(f"[MS] microswitch pressed")
            
            # Check if we're in NFC encoding mode
            nfc_encoding_album_id = self.app.state.get(NFC_ENCODING_ALBUM_ID)
            nfc_write_state  = self.app.state.get(NFC_WRITE_STATE)
            self.app.logger.debug(f"[MS] write_nfc_data called — album_id: {nfc_encoding_album_id}, write_state: {nfc_write_state}")
            if nfc_write_state:
                # ENCODING MODE: Write the album ID to the card
                response = {
                    "type": "nfc_encoding_started",
                    "payload": {"album_id": nfc_encoding_album_id}
                }
                await self.app.ws.send(json.dumps(response))
                self.app.logger.info(f"[MS] ENCODING MODE - Writing album_id: {nfc_encoding_album_id}")
                # TODO: Add display message for encoding mode

                await self.write_nfc_data(nfc_encoding_album_id, "MS-WRITE")
            else:
                # NORMAL MODE: Read from the card
                self.app.logger.info(f"[MS] NORMAL MODE - Reading card")
                self.app.display.show_message("Reading NFC card...", duration=5)
                await self.handle_read_nfc()
        except Exception as e:
            self.app.logger.error(f"[MS] error handling microswitch press: {e}")

    async def handle_card_scanned(self, album_id):
        """Handle NFC card scan with album ID."""
        import gc, json

        self.app.logger.info(f"[PLAY] card scanned — album ID: {album_id}")
        
        gc.collect()  # Free memory before WS call
        msg = {
            "type": "play_album",
            "payload": {
                "album_id": album_id
            }
        }
        try:
            await self.app.ws.send(json.dumps(msg))
            self.app.state.set({LAST_NFC_SCAN: f"Album ID: {album_id}"})
            self.app.logger.info(f"[PLAY] requested to play album_id={album_id}")
        except Exception as e:
            self.app.logger.error(f"[PLAY] failed to send play album command: {e}")
        gc.collect()  # Free memory after WS call


    async def write_nfc_data(self, album_id, context=""):
        """Unified NFC write handler - send result to backend.
        
        Args:
            album_id: Album ID to write to card
            context: Log context prefix (e.g., 'NFC-BG', 'MS-WRITE')
        """

        import json
        
        if self.app.nfc is None:
            if context:
                self.app.logger.warn(f"[{context}] NFC reader not available — skipping write")
            return
        
        try:
            if context:
                self.app.logger.info(f"[{context}] starting write_data for album_id: {album_id}")
            
            result = await self.app.nfc.write_data(album_id, timeout_ms=30000)
            
            if context:
                self.app.logger.info(f"[{context}] result — status: {result.get('status')}, uid: {result.get('uid')}")
            
            # Send completion message back to backend
            response = {
                "type": "nfc_encoding_complete",
                "payload": result
            }
            await self.app.ws.send(json.dumps(response))
            
            if context:
                self.app.logger.info(f"[{context}] encoding result sent to backend")
        except Exception as e:
            if context:
                self.app.logger.error(f"[{context}] error: {e}")
            
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
                    self.app.logger.error(f"[{context}] failed to send error: {send_error}")
        finally:
            # Clear encoding flag
            if self.app.state.get(NFC_WRITE_STATE) == True:
                self.app.state.set({NFC_WRITE_STATE: False})
                if context:
                    self.app.logger.info(f"[{context}] Encoding mode cleared")


    async def handle_read_nfc(self):
        """Read card via microswitch trigger."""
        import asyncio

        if self.app.nfc is None:
            self.app.logger.warn(f"[MS-READ] NFC reader not available")
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
            self.app.logger.error(f"[MS-READ] error: {e}")