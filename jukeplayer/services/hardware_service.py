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

