"""Button press action handlers."""

import json
import gc
from jukeplayer.core.state_constants import *


class ButtonHandler:
    """Maps (button_name, press_type) to actions."""

    # Allowed button identifiers.  Any dispatch() with a name not in this
    # set is silently ignored (hardening against misconfigured hardware).
    ALLOWED_BUTTONS = frozenset([
        "play_pause",
        "next_track",
        "previous_track",
        "stop",
        "encoder_sw",
        "nfc_card",
        "utility_button",
    ])

    # Supported press classifications.
    PRESS_TYPES = frozenset(["single", "double", "long"])

    # Buttons that forward a single press as a WebSocket command.
    # NOTE: "utility_button" is intentionally absent — its single press is
    # bound to _handle_backlight_toggle below; the generic sender loop would
    # register a handler here that is immediately overwritten.
    WS_SINGLE_COMMANDS = [
        "play_pause",
        "next_track",
        "previous_track",
        "stop",
    ]

    def __init__(self, app):
        self.app = app
        self._handlers = {}

        # Generic WS single-press handlers
        for name in self.WS_SINGLE_COMMANDS:
            self._register((name, "single"), self._make_ws_sender(name))

        # Specific handlers
        self._register("nfc_card", self._handle_nfc_card)
        self._register(("stop", "long"), self._handle_hardware_reset)
        self._register(("utility_button", "long"), self._handle_volume_mute)
        self._register(("utility_button", "double"), self._handle_repeat_toggle)
        self._register(("utility_button", "single"), self._handle_backlight_toggle)


    def _get_label(self, button_name):
        """Return label for a button from its pin config, falling back to button_name."""
        try:
            return self.app.config["hardware"]["buttons"]["pins"][button_name]["label"]
        except (KeyError, AttributeError):
            return button_name

    def _register(self, key, handler):
        """key can be a string (matches any press_type) or tuple (name, type)."""
        self._handlers[key] = handler

    async def dispatch(self, button_name, press_type="single"):
        """Route event to the appropriate handler."""
        # Hardening: reject unknown buttons
        if button_name not in self.ALLOWED_BUTTONS:
            self.app.logger.info(f"Ignored disallowed button: {button_name}")
            return

        # Hardening: reject unknown press types
        if press_type not in self.PRESS_TYPES:
            self.app.logger.info(f"Ignored unknown press type: {press_type}")
            return

        self.app.logger.info(f"Button: {button_name} ({press_type})")
        gc.collect()

        handler = self._handlers.get((button_name, press_type))
        if handler is None:
            handler = self._handlers.get(button_name)

        if handler is None:
            self.app.logger.info(f"No handler for {button_name} ({press_type})")
            return

        try:
            await handler()
        except Exception as e:
            self.app.logger.info(f"Handler error for {button_name}/{press_type}: {e}")
        finally:
            gc.collect()

    # --- factories & handlers ---

    def _make_ws_sender(self, command):
        async def _sender():
            label = self._get_label(command)
            # Send the command immediately so the backend responds fast.
            await self.app.ws.send(json.dumps({"type": command, "payload": {}}))
            self.app.logger.info(f"WS command sent: {command}")
            # Then show the overlay without waiting for the screen refresh.
            self.app.display.show_message(f"{label} pressed", duration=2)
        return _sender

    async def _handle_nfc_card(self):
        from jukeplayer.services.nfc_service import NFCService
        nfc_service = NFCService(self.app)
        await nfc_service.handle_microswitch_press()
        self.app.logger.info("NFC card detected via microswitch")

    async def _handle_backlight_toggle(self):
        self.app.logger.info("Toggling backlight")
        # display.backlight is a raw machine.Pin (no .toggle() on the ESP32
        # port); the managers expose toggle_backlight() for this.
        self.app.display.toggle_backlight()

    async def _handle_repeat_toggle(self):
        await self.app.ws.send(json.dumps({"type": "toggle_repeat", "payload": {}}))
        current = bool(self.app.state.get(REPEAT_STATUS, False))
        self.app.state.set({REPEAT_STATUS: not current})
        self.app.logger.info(f"Repeat status toggled")

    async def _handle_volume_mute(self):
        await self.app.ws.send(json.dumps({"type": "volume_mute", "payload": {}}))
        current = bool(self.app.state.get(MUTED, False))
        self.app.state.set({MUTED: not current})
        self.app.logger.info(f"Volume mute toggled")

    async def _handle_hardware_reset(self):
        """Reset hardware components to a known state."""
        self.app.logger.info("Resetting hardware...")
        import machine
        machine.reset() 