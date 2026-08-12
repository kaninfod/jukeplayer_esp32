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

        # Backlight control (active low on many panels, but we keep the same
        # contract as the other managers: value(1) = on).
        from machine import Pin

        self.backlight = Pin(backlight_pin, Pin.OUT, value=1)

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

    def toggle_backlight(self):
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

        # Use a light theme background.
        self.scr = lv.screen_active()
        if self.scr is None:
            self.scr = lv.obj()
            lv.scr_load(self.scr)
        self.scr.set_style_bg_color(lv.color_white(), 0)
        self.scr.set_style_bg_opa(lv.OPA.COVER, 0)

        # Fonts. LVGL ships these by default.
        self.font_symbol = lv.font_montserrat_16
        self.font_small = lv.font_montserrat_16
        self.font_medium = lv.font_montserrat_20
        self.font_large = lv.font_montserrat_24

        # Status indicators use plain text for now. LVGL symbols require the
        # symbol font to be enabled in lv_conf.h; that can be added later.
        self._status_map = {
            "WS:OK": ("NET", lv.palette_main(lv.PALETTE.GREEN)),
            "WS:CON": ("NET", lv.palette_main(lv.PALETTE.BLUE)),
            "WS:ERR": ("NET", lv.palette_main(lv.PALETTE.RED)),
            "WIFI_DISCONNECTED": ("NET", lv.palette_main(lv.PALETTE.RED)),
        }
        self._player_map = {
            "PLAY": ">",
            "PAUSE": "||",
            "STOP": "[]",
            "BOOT": "[]",
            "IDLE": "[]",
        }
        self._repeat_text = " RPT"
        self._repeat = False
        self._player_status_text = ""

        self._build_top_bar()
        self._build_album_art()
        self._build_metadata()

    def _build_top_bar(self):
        """Top status bar: network indicator, volume, player status."""
        bar = lv.obj(self.scr)
        bar.set_size(self.width, 32)
        bar.align(lv.ALIGN.TOP_MID, 0, 4)
        bar.set_style_bg_color(lv.color_white(), 0)
        bar.set_style_border_width(0, 0)
        bar.set_style_pad_all(0, 0)
        bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # Network status
        self.label_net = lv.label(bar)
        self.label_net.set_style_text_font(self.font_symbol, 0)
        self.label_net.set_style_text_color(lv.palette_main(lv.PALETTE.BLUE), 0)
        self.label_net.align(lv.ALIGN.LEFT_MID, 8, 0)
        self.label_net.set_text("NET")

        # Volume centered
        self.label_volume = lv.label(bar)
        self.label_volume.set_style_text_font(self.font_small, 0)
        self.label_volume.set_style_text_color(lv.color_black(), 0)
        self.label_volume.align(lv.ALIGN.CENTER, 0, 0)
        self.label_volume.set_text("--")

        # Player status + repeat on the right
        self.label_player_status = lv.label(bar)
        self.label_player_status.set_style_text_font(self.font_symbol, 0)
        self.label_player_status.set_style_text_color(lv.color_black(), 0)
        self.label_player_status.align(lv.ALIGN.RIGHT_MID, -8, 0)
        self.label_player_status.set_text("")

    def _build_album_art(self):
        """Centered album art placeholder."""
        art_size = 180
        self.art_size = art_size
        self.art_col = (self.width - art_size) // 2
        self.art_row = 50  # below top bar

        self.album_art = lv.obj(self.scr)
        self.album_art.set_size(art_size, art_size)
        self.album_art.set_pos(self.art_col, self.art_row)
        self.album_art.set_style_bg_color(lv.palette_main(lv.PALETTE.BLUE_GREY), 0)
        self.album_art.set_style_radius(8, 0)
        self.album_art.clear_flag(lv.obj.FLAG.SCROLLABLE)

        self.album_art_label = lv.label(self.album_art)
        self.album_art_label.set_text("")
        self.album_art_label.center()

    def _build_metadata(self):
        """Artist, album, title labels below the art."""
        margin = 12
        text_w = self.width - margin * 2
        y = self.art_row + self.art_size + 16

        self.label_artist = lv.label(self.scr)
        self.label_artist.set_style_text_font(self.font_large, 0)
        self.label_artist.set_style_text_color(lv.color_black(), 0)
        self.label_artist.set_width(text_w)
        self.label_artist.set_pos(margin, y)
        self.label_artist.set_text("")

        y += 36
        self.label_album = lv.label(self.scr)
        self.label_album.set_style_text_font(self.font_small, 0)
        self.label_album.set_style_text_color(lv.palette_main(lv.PALETTE.GREY), 0)
        self.label_album.set_width(text_w)
        self.label_album.set_pos(margin, y)
        self.label_album.set_text("")

        y += 28
        self.label_title = lv.label(self.scr)
        self.label_title.set_style_text_font(self.font_medium, 0)
        self.label_title.set_style_text_color(lv.color_black(), 0)
        self.label_title.set_width(text_w)
        self.label_title.set_pos(margin, y)
        self.label_title.set_text("")

    def show_on_display(self):
        """Ensure this screen is active."""
        lv.scr_load(self.scr)

    def set_initial_boot_state(self):
        self._message_active = False
        self.label_artist.set_text("Jukeplayer")
        self.label_album.set_text("")
        self.label_title.set_text("Booting...")
        self._set_net_status("WS:CON")
        self._set_player_status("BOOT")
        self.label_volume.set_text("--")
        self.album_art_label.set_text("")

    def update(self, state={}):
        if self._message_active:
            return

        if NETWORK_STATUS in state:
            self._set_net_status(state[NETWORK_STATUS])
        if VOLUME in state:
            self._set_volume(state[VOLUME])
        if PLAYER_STATUS in state:
            self._set_player_status(state[PLAYER_STATUS])
        if REPEAT_STATUS in state:
            self._set_repeat_status(state[REPEAT_STATUS])
        if ARTIST in state:
            self.label_artist.set_text(str(state[ARTIST] or ""))
        if ALBUM in state:
            self.label_album.set_text(str(state[ALBUM] or ""))
        if TITLE in state:
            self.label_title.set_text(str(state[TITLE] or ""))
        if COVER_URL in state:
            self._set_cover_url(state[COVER_URL])

    def _set_net_status(self, status):
        status = str(status).upper()
        text, color = self._status_map.get(status, ("NET", lv.palette_main(lv.PALETTE.RED)))
        self.label_net.set_text(text)
        self.label_net.set_style_text_color(color, 0)

    def _set_player_status(self, status):
        status = str(status).upper()
        self._player_status_text = self._player_map.get(status, "")
        self._refresh_repeat()

    def _set_repeat_status(self, repeat):
        self._repeat = bool(repeat)
        self._refresh_repeat()

    def _refresh_repeat(self):
        text = self._player_status_text
        if self._repeat:
            text += self._repeat_text
        self.label_player_status.set_text(text)

    def _set_volume(self, volume):
        self.label_volume.set_text(f"{volume}%")

    def _set_cover_url(self, url):
        """Placeholder for cover art loading.

        In the first iteration we just show a placeholder text. A future version
        can download the RGB565 cover and update an lv.img widget.
        """
        if url:
            self.album_art_label.set_text("[cover]")
        else:
            self.album_art_label.set_text("")

    def show_message(self, text, header="Message"):
        self._message_active = True
        self.label_artist.set_text(str(header))
        self.label_album.set_text("")
        self.label_title.set_text(str(text))
        self.album_art_label.set_text("")

    def clear_message(self):
        self._message_active = False
