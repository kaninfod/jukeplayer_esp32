from jukeplayer.core.state_constants import *
from jukeplayer.services.button_handler import ButtonHandler

class HardwareService: 
    def __init__(self, app):
        self.app = app
        self.button_handler = ButtonHandler(app)

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
        """Route button events to the handler."""
        await self.button_handler.dispatch(button_name, press_type)
    
    # async def handle_button_press(self, button_name, press_type="single"):
    #     """Handle button press by sending command via WebSocket.
        
    #     Send control command via WebSocket /events endpoint.
    #     """
    #     import gc, json
    #     self.app.logger.info(f"Button pressed: {button_name} with press type: {press_type}")
        
    #     gc.collect()  # Free memory before sending
    #     success = False
        
    #     # allowed commands from for ws action
    #     ws_command_whitelist = ["play_pause", "next_track", "previous_track", "stop"]
        
    #     if button_name in ws_command_whitelist and press_type == "single":
    #         try:
    #             self.app.display.show_message(f"{button_name} pressed ", duration=5)
    #             await self.app.ws.send(json.dumps( { "type": button_name, "payload": {} } ))

    #             success = True
    #         except Exception as e:
    #             self.app.logger.info(f"Failed to send {button_name} command: {e}")
    #             success = False
    #     elif button_name == "nfc_card":
    #         from jukeplayer.services.nfc_service import NFCService
    #         nfc_service = NFCService(self.app)
    #         await nfc_service.handle_microswitch_press()
    #         self.app.logger.info(f"NFC card detected via microswitch")
    #         success = True
    #     elif button_name == "encoder_sw" and press_type == "long":
    #         if self.app.display.get_current_layout() != "message":
    #             self.app.logger.info(f"Switching to 'message' layout")
    #             self.app.display.show_message("Encoder SW long press detected and will stay active for 10 sec.", duration=10)
    #         else:
    #             self.app.logger.info(f"Switching back to 'status' layout")
    #             self.app.display.switch_layout("status")
    #         success = True
    #     elif button_name == "play_pause" and press_type == "long":
    #             #import machine
    #             #machine.reset()
    #             self.app.logger.info(f"Long press on play_pause button detected - toggling backlight")
    #             self.app.display.backlight.toggle()
    #             success = True
    #     elif button_name == "stop" and press_type == "long":
    #         try:
    #             await self.app.ws.send(json.dumps( { "type": "volume_mute", "payload": {} } ))
    #             self.app.state.set({MUTE_STATUS: not bool(self.app.state.get(MUTE_STATUS, False))})
    #             self.app.logger.info(f"Volume mute command sent")
    #             success = True
    #         except Exception as e:
    #             self.app.logger.info(f"Failed to send {button_name} command: {e}")
    #             success = False

    #     if not success:
    #         self.app.logger.info(f"{button_name} command not supported for press type {press_type}")
    #     gc.collect()  # Free memory after sending            

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
        # self.app.display.set_volume(current_volume)
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