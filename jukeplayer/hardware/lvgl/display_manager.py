from jukeplayer.core.state_constants import *
from jukeplayer.core.logger import log
import asyncio

# The C driver module is compiled into the firmware as a user C module.
# It registers the ILI9488 display with LVGL and exposes ili9488_lvgl.init().
try:
    import ili9488_lvgl
except ImportError:
    ili9488_lvgl = None

try:
    import lvgl as lv
except ImportError:
    lv = None


class DisplayManager:
    """LVGL-based ILI9488 display manager.

    Mirrors the contract of the existing ILI9488/ST7735R nano-gui managers so
    app.py and HardwareFactory can swap it in without changes.
    """

    def __init__(
        self,
        spi,
        app_state=None,
        cs=15,
        dc=2,
        rst=4,
        backlight_pin=32,
        backlight_active_low=True,
        width=480,
        height=320,
        usd=False,
        mirror=False,
        color_invert=False,
        init_spi=None,
        cover_base_url=None,
        spi_baudrate=24000000,
        nfc_cs=None,
    ):
        if lv is None:
            raise RuntimeError("LVGL module not available in firmware")
        if ili9488_lvgl is None:
            raise RuntimeError("ili9488_lvgl C driver not available in firmware")

        self.width = width
        self.height = height
        self.app_state = app_state

        log.info(f"[LVGL] creating display {width}x{height}")

        # Always initialize LVGL explicitly. The auto-init on import may have
        # run before PSRAM was fully ready, so force it here.
        lv.init()
        log.info("[LVGL] initialized")

        # Set the shared SPI bus to the display speed before the C driver
        # takes over. The C driver will not switch speeds itself.
        spi.init(baudrate=spi_baudrate)
        if nfc_cs is not None:
            from machine import Pin
            Pin(nfc_cs, Pin.OUT).value(1)

        ili9488_lvgl.init(
            spi=spi,
            cs=cs,
            dc=dc,
            rst=rst,
            width=width,
            height=height,
            usd=usd,
            mirror=mirror,
            color_invert=color_invert,
            nfc_cs=nfc_cs,
        )

        # Backlight control: plain on/off GPIO. Active-low means 0 = on.
        from machine import Pin

        self.backlight_active_low = backlight_active_low
        self.backlight = Pin(backlight_pin, Pin.OUT)
        self.set_brightness(100)

        # Build the LVGL UI.
        self.current_layout = "status"
        self.current_screen = StatusScreen(
            width=width, height=height, cover_base_url=cover_base_url
        )
        self.current_screen.show_on_display()

        self.is_running = False
        self._lv_task = None
        self.timer_task = None

        log.info("[LVGL] display manager initialized")

    def show_message(self, message, duration=5):
        if self.timer_task:
            try:
                self.timer_task.cancel()
            except RuntimeError:
                pass
            self.timer_task = None

        self.current_layout = "message"
        self.current_screen.show_message(str(message), header="Message")

        if duration is not None:
            self.timer_task = asyncio.create_task(
                self._layout_timer_loop(duration, "status")
            )

    def set_brightness(self, percent):
        """Set backlight brightness 0-100% (currently on/off only)."""
        percent = max(0, min(100, int(percent)))
        if self.backlight_active_low:
            self.backlight.value(0 if percent > 0 else 1)
        else:
            self.backlight.value(1 if percent > 0 else 0)

    def toggle_backlight(self):
        """Toggle backlight on/off."""
        self.backlight.value(0 if self.backlight.value() else 1)

    def switch_layout(self, layout_name, duration=None, fallback_layout=None):
        if self.timer_task:
            try:
                self.timer_task.cancel()
            except RuntimeError:
                pass
            self.timer_task = None

        if layout_name == "status":
            self.current_layout = layout_name
            self.current_screen.clear_message()
            current_state = self.app_state.data if self.app_state else {}
            self.current_screen.update(current_state)

            if duration is not None and fallback_layout == "message":
                self.timer_task = asyncio.create_task(
                    self._layout_timer_loop(duration, fallback_layout)
                )
        elif layout_name == "message":
            # show_message is the public entry point; this branch exists for
            # API compatibility with the other managers.
            self.current_layout = layout_name

    async def _layout_timer_loop(self, duration, fallback_layout):
        try:
            await asyncio.sleep(duration)
            self.timer_task = None
            self.switch_layout(fallback_layout)
        except asyncio.CancelledError:
            pass
        finally:
            if self.timer_task == asyncio.current_task():
                self.timer_task = None

    def get_current_layout(self):
        return self.current_layout

    def update(self, state):
        """Update widgets from an AppState delta."""
        self.current_screen.update(state)

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.current_screen.set_initial_boot_state()
            self._lv_task = asyncio.create_task(self._lvgl_task_loop())
            log.info("[LVGL] display manager started")

    def stop(self):
        self.is_running = False
        if self._lv_task:
            try:
                self._lv_task.cancel()
            except RuntimeError:
                pass
            self._lv_task = None
        if self.timer_task:
            try:
                self.timer_task.cancel()
            except RuntimeError:
                pass
            self.timer_task = None

    async def _lvgl_task_loop(self):
        """Drive LVGL's task handler from the asyncio event loop."""
        while self.is_running:
            try:
                lv.task_handler()
                await asyncio.sleep_ms(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[LVGL] task handler error: {e}")
                await asyncio.sleep_ms(100)


class StatusScreen:
    """Main jukebox status screen built from LVGL widgets."""

    def __init__(self, width=480, height=320, cover_base_url=None):
        self.width = width
        self.height = height
        self.cover_base_url = cover_base_url
        self._message_active = False

        # Create a fresh screen and load it (no styling yet).
        self.scr = lv.obj()
        lv.screen_load(self.scr)

        # Minimal placeholder label.
        self.label_artist = lv.label(self.scr)
        self.label_artist.set_text("Jukeplayer")
        self.label_artist.center()

    def _build_top_bar(self):
        pass

    def _build_album_art(self):
        pass

    def _build_metadata(self):
        pass

    def show_on_display(self):
        """Ensure this screen is active."""
        lv.screen_load(self.scr)

    def set_initial_boot_state(self):
        self.label_artist.set_text("Booting...")

    def update(self, state={}):
        if self._message_active:
            return
        if ARTIST in state:
            self.label_artist.set_text(str(state[ARTIST] or ""))

    def show_message(self, text, header="Message"):
        self._message_active = True
        self.label_artist.set_text(str(text))

    def clear_message(self):
        self._message_active = False
