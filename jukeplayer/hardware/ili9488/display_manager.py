from jukeplayer.core.state_constants import *
from jukeplayer.core.logger import log
import asyncio
import time
from machine import Pin
from jukeplayer.nanogui.core.writer import CWriter
from jukeplayer.nanogui.widgets.label import Label, ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT
from jukeplayer.nanogui.fonts import geistmonobold24, geistmonobold18, geistmonobold14, material_subset
import jukeplayer.hardware.ili9488.ili9488 as ili9488


class CoverArtDownloader:
    """Download a raw RGB565 cover image and blit it into the ILI9488 frame buffer."""

    def __init__(self, display, art_col, art_row, art_size, cover_base_url=None):
        self.display = display
        self.art_col = art_col
        self.art_row = art_row
        self.art_size = art_size
        self.cover_base_url = cover_base_url
        self._last_cover_data = None
        self._fetching = False
        self._wanted_url = None

    def _device_url(self, url):
        if url.startswith("/"):
            base = self.cover_base_url or ""
            if not base:
                log.error("[COVER] relative URL but no cover_base_url configured")
            url = base + url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}format=rgb565"

    async def fetch_and_blit(self, url):
        """Fetch a cover and blit it into the framebuffer.

        The newest requested URL always wins: if the album changed while a
        download was in flight, the completed fetch is immediately followed
        by the newer one instead of being silently dropped.
        """
        if not url:
            return False
        self._wanted_url = url
        if self._fetching:
            return False  # the in-flight fetch chases this URL when it finishes

        self._fetching = True
        try:
            while True:
                url = self._wanted_url
                device_url = self._device_url(url)
                log.info(f"[COVER] fetching {device_url}")
                body = await self._http_get(device_url, timeout=10)
                expected = self.art_size * self.art_size * 2
                if len(body) < expected:
                    log.warn(f"[COVER] received {len(body)} bytes, expected {expected}")
                    return False
                self._last_cover_data = body[:expected]
                # hold the display lock so a segmented refresh cannot transfer
                # a half-written cover (torn art)
                async with self.display._lock:
                    self._blit_rgb565(self._last_cover_data)
                if self._wanted_url == url:
                    return True
                log.info("[COVER] newer cover requested mid-download - chasing")
        except Exception as e:
            log.error(f"[COVER] download failed: {e}")
            return False
        finally:
            self._fetching = False

    def blit_last(self):
        """Re-blit the most recently fetched cover. Useful after a full redraw."""
        if self._last_cover_data is None:
            return False
        self._blit_rgb565(self._last_cover_data)
        return True

    async def _http_get(self, url, timeout=10):
        """Fetch over a cooperatively-scheduled socket: connect, send and all
        reads yield to the event loop, so a slow cover server cannot stall
        WS pings, buttons or the display.
        """
        proto, _, hostpath = url.partition("://")
        if proto not in ("http", ""):
            raise ValueError("Only HTTP URLs supported")
        host, _, path = hostpath.partition("/")
        path = "/" + path
        port = 80
        if ":" in host:
            host, port = host.split(":")
            port = int(port)

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout
        )
        try:
            request = f"GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()

            chunks = []
            deadline = time.ticks_add(time.ticks_ms(), timeout * 1000)
            while True:
                remaining_ms = time.ticks_diff(deadline, time.ticks_ms())
                if remaining_ms <= 0:
                    raise TimeoutError("cover download timed out")
                try:
                    chunk = await asyncio.wait_for(
                        reader.read(1024), remaining_ms / 1000
                    )
                except asyncio.TimeoutError:
                    raise TimeoutError("cover download timed out")
                if not chunk:
                    break
                chunks.append(chunk)
            response = b"".join(chunks)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

        header_end = response.find(b"\r\n\r\n")
        if header_end < 0:
            raise ValueError("Invalid HTTP response")
        return response[header_end + 4 :]

        header_end = response.find(b"\r\n\r\n")
        if header_end < 0:
            raise ValueError("Invalid HTTP response")
        return response[header_end + 4 :]

    def _blit_rgb565(self, data):
        """Blit a 180x180 RGB565 image into the display frame buffer at the art location."""
        mvb = self.display.mvb
        disp_w = self.display.width
        art_w = self.art_size
        art_h = self.art_size
        col = self.art_col
        row = self.art_row
        src_bytes_per_row = art_w * 2

        for y in range(art_h):
            dst_row_start = ((row + y) * disp_w + col) * 2
            src_row_start = y * src_bytes_per_row
            mvb[dst_row_start : dst_row_start + src_bytes_per_row] = data[
                src_row_start : src_row_start + src_bytes_per_row
            ]


class DisplayManager:
    """ILI9488 color LCD display manager aligned to the OLED/ST7735R API."""

    def __init__(
        self,
        spi,
        app_state=None,
        cs=15,
        dc=2,
        rst=4,
        backlight_pin=32,
        backlight_active_low=True,
        width=320,
        height=480,
        usd=True,
        mirror=False,
        color_invert=False,
        init_spi=None,
        cover_base_url=None,
    ):
        self.width = width
        self.height = height
        self.app_state = app_state
        self.backlight_active_low = backlight_active_low

        log.info(f"[ILI9488] creating display {width}x{height} usd={usd} mirror={mirror}")
        if color_invert:
            ili9488.ILI9488.COLOR_INVERT = 0xFFFF
            log.info("[ILI9488] COLOR_INVERT set to 0xFFFF")

        driver_class = ili9488.ILI9488_RGB565
        log.info("[ILI9488] using RGB565 (65K color) driver")

        self.display = driver_class(
            spi=spi,
            cs=self._as_pin(cs, Pin),
            dc=self._as_pin(dc, Pin),
            rst=self._as_pin(rst, Pin),
            height=self.height,
            width=self.width,
            usd=usd,
            mirror=mirror,
            init_spi=init_spi or False,
            lines_per_write=4,
        )

        log.info(f"[ILI9488] backlight pin {backlight_pin} on (active_low={self.backlight_active_low})")
        self.backlight = Pin(backlight_pin, Pin.OUT)
        self.backlight.value(0 if self.backlight_active_low else 1)

        from jukeplayer.nanogui.core.writer import CWriter
        self.BLACK = CWriter.create_color(self.display, 0, 0, 0, 0)
        self.WHITE = CWriter.create_color(self.display, 1, 255, 255, 255)
        self.RED = CWriter.create_color(self.display, 2, 255, 0, 0)
        self.BLUE = CWriter.create_color(self.display, 3, 0, 100, 150)
        self.BROWN = CWriter.create_color(self.display, 4, 150, 80, 0)
        log.info("[ILI9488] palette loaded")

        self.writers = (
            CWriter(self.display, material_subset, fgcolor=self.BROWN, bgcolor=self.WHITE, verbose=False),
            CWriter(self.display, geistmonobold14, fgcolor=self.BLACK, bgcolor=self.WHITE, verbose=False),
            CWriter(self.display, geistmonobold18, fgcolor=self.BLACK, bgcolor=self.WHITE, verbose=False),
            CWriter(self.display, geistmonobold24, fgcolor=self.BLUE, bgcolor=self.WHITE, verbose=False),
        )

        log.info("[ILI9488] creating StatusScreen")
        self.current_screen = StatusScreen(
            self.display,
            self.writers,
            palette=self,
            cover_base_url=cover_base_url,
        )
        self.current_screen.display_manager = self

        initial_state = self.app_state.data if self.app_state else {}
        self.current_screen.update(initial_state)
        log.info("[ILI9488] drawing static screen")
        self.current_screen.draw_static()
        log.info("[ILI9488] calling display.show()")
        self.display.show()
        log.info("[ILI9488] initial refresh complete")

        self.is_running = False
        self._message_timer_task = None
        self._refresh_task = None
        self._refresh_debounce_ms = 50

    def _schedule_refresh(self):
        """Schedule a debounced async refresh so multiple rapid state updates
        collapse into a single SPI transfer and yield during the long transfer.
        """
        if self._refresh_task and not self._refresh_task.done():
            try:
                self._refresh_task.cancel()
            except Exception:
                pass
        self._refresh_task = asyncio.create_task(self._run_refresh())

    async def _run_refresh(self):
        """Wait a short debounce, then refresh the display using the async
        segment-based refresh so other tasks can run during SPI transfer.
        """
        try:
            await asyncio.sleep_ms(self._refresh_debounce_ms)
            if hasattr(self.display, "do_refresh"):
                await self.display.do_refresh(split=4)
            else:
                self.display.show()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"[ILI9488] refresh task error: {e}")
        finally:
            # only clear the reference if WE are still the active task — a
            # cancelled refresh must not clobber its replacement's reference
            if self._refresh_task == asyncio.current_task():
                self._refresh_task = None

    @staticmethod
    def _as_pin(pin_or_num, pin_type):
        if hasattr(pin_or_num, "value"):
            return pin_or_num
        return pin_type(int(pin_or_num), Pin.OUT)

    def show_message(self, message, duration=5):
        """Show a temporary message overlay on the current screen."""
        if self._message_timer_task:
            try:
                self._message_timer_task.cancel()
            except RuntimeError:
                pass
            self._message_timer_task = None

        self.current_screen.show_message(str(message), header="Message")
        self._schedule_refresh()

        if duration is not None:
            self._message_timer_task = asyncio.create_task(
                self._message_timeout(duration)
            )

    async def _message_timeout(self, duration):
        """Clear the message after the given duration."""
        try:
            await asyncio.sleep(duration)
            self._message_timer_task = None
            self.current_screen.clear_message()
            self._schedule_refresh()
        except asyncio.CancelledError:
            pass
        finally:
            if self._message_timer_task == asyncio.current_task():
                self._message_timer_task = None

    def set_brightness(self, percent):
        """Set backlight brightness 0-100%% (currently on/off only)."""
        percent = max(0, min(100, int(percent)))
        if self.backlight_active_low:
            self.backlight.value(0 if percent > 0 else 1)
        else:
            self.backlight.value(1 if percent > 0 else 0)

    def toggle_backlight(self):
        self.backlight.value(0 if self.backlight.value() else 1)

    def draw_test_pattern(self):
        """Public hook to fill the album-art area with an RGB565 gradient."""
        self.current_screen._draw_test_pattern()
        self._schedule_refresh()

    def update(self, state):
        for key in state:
            if key not in NON_VISUAL_KEYS:
                break
        else:
            return  # delta contains only non-visual keys — no repaint needed
        self.current_screen.update(state)
        self._schedule_refresh()

    def start(self):
        if not self.is_running:
            self.is_running = True
            log.info("[ILI9488] start()")
            if hasattr(self.current_screen, "set_initial_boot_state"):
                self.current_screen.set_initial_boot_state()
                self._schedule_refresh()

    def stop(self):
        self.is_running = False
        if self._message_timer_task:
            self._message_timer_task.cancel()
            self._message_timer_task = None


class AlbumArtPlaceholder:
    """Draws a centered rectangle as a placeholder for album art."""

    def __init__(self, display, size=180, row=64, fgcolor=None, bgcolor=None, palette=None):
        self.display = display
        self.size = size
        if palette is None:
            fgcolor = fgcolor if fgcolor is not None else display.rgb(0, 100, 150)
            bgcolor = bgcolor if bgcolor is not None else display.rgb(0, 0, 0)
        else:
            fgcolor = fgcolor if fgcolor is not None else palette.BLUE
            bgcolor = bgcolor if bgcolor is not None else palette.BLACK
        self.fgcolor = fgcolor
        self.bgcolor = bgcolor
        self.col = 10
        self.row = row  # below top bar + 15 px gap

    def show(self):
        self.display.fill_rect(self.col, self.row, self.size, self.size, self.fgcolor)
        self.display.rect(self.col, self.row, self.size, self.size, self.bgcolor)


class DualLineLabel:
    """A label that renders across two stacked lines, auto-wrapping and
    truncating the second line with '...' if needed. Reports its actual
    rendered height so layouts can collapse empty/single-line slots.
    """

    def __init__(self, writer, col, width, line_gap=2, bdcolor=False):
        self.writer = writer
        self.col = col
        self.width = width
        self.line_gap = line_gap
        self.text = ""
        self._row = 0
        self._line1_text = ""
        self._line2_text = ""
        self._line1 = Label(writer, 0, col, width, align=ALIGN_LEFT, bdcolor=bdcolor)
        self._line1.invert = False
        self._line2 = Label(writer, 0, col, width, align=ALIGN_LEFT, bdcolor=bdcolor)
        self._line2.invert = False

    def set_row(self, row):
        self._row = row
        h = self.writer.height
        self._line1.row = row
        self._line2.row = row + h + self.line_gap

    def height(self):
        if not self.text:
            return 0
        if self._line2_text:
            return self.writer.height * 2 + self.line_gap
        return self.writer.height

    def show(self):
        self._line1.show()
        if self._line2_text:
            self._line2.show()

    def set_text(self, text):
        text = str(text or "")
        self.text = text
        self._line1_text, self._line2_text = self._compute_lines(text)
        self._line1._value = self._line1_text or None
        self._line2._value = self._line2_text or None

    def _compute_lines(self, text):
        if not text:
            return "", ""
        if self.writer.stringlen(text) <= self.width:
            return text, ""
        words = text.split()
        if not words:
            return text, ""
        best = 0
        for i in range(1, len(words) + 1):
            candidate = " ".join(words[:i])
            if self.writer.stringlen(candidate) <= self.width:
                best = i
            else:
                break
        if best == 0:
            line1 = self._fit_with_ellipsis(words[0])
            remaining = " ".join(words[1:])
        else:
            line1 = " ".join(words[:best])
            remaining = " ".join(words[best:])
        line2 = self._fit_with_ellipsis(remaining) if remaining else ""
        return line1, line2

    def _fit_with_ellipsis(self, text):
        if not text or self.writer.stringlen(text) <= self.width:
            return text
        ellipsis = "..."
        if self.writer.stringlen(ellipsis) > self.width:
            return ""
        lo, hi = 0, len(text)
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = text[:mid] + ellipsis
            if self.writer.stringlen(candidate) <= self.width:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best == 0:
            return ""
        return text[:best] + ellipsis


class StatusScreen:
    def __init__(self, display, writers, palette=None, cover_base_url=None):
        log.info("[ILI9488] StatusScreen.__init__")
        self.display = display
        self.palette = palette
        self.writer_symbols = writers[0]
        self.writer_mini = writers[1]
        self.writer_small = writers[2]
        self.writer_large = writers[3]

        safe_width = max(1, self.display.width - 1)
        top_row = 2
        art_size = 180
        art_row = 64

        label_col = art_size + 20
        status_icon_w = 96  # wide enough for net + player + repeat + mute icons

        # Layout constants for the metadata column (right of cover art).
        self.safe_width = safe_width
        self.label_col = label_col
        self.art_row = art_row

        # Top status bar: volume on left, combined icons on right.
        self.label_volume = Label(
            self.writer_small, top_row, 0, label_col, align=ALIGN_LEFT, bdcolor=False
        )
        self.label_status = Label(
            self.writer_symbols, top_row, safe_width - status_icon_w, status_icon_w,
            align=ALIGN_RIGHT, bdcolor=False,
        )

        # Cached icon state for the combined status label.
        self._net_icon = "\ue648"
        self._player_icon = ""
        self._repeat_icon = ""
        self._mute_icon = ""

        # Album art placeholder.
        self.album_art = AlbumArtPlaceholder(display, palette=palette)
        self.downloader = CoverArtDownloader(
            self.display,
            self.album_art.col,
            self.album_art.row,
            self.album_art.size,
            cover_base_url=cover_base_url,
        )

        # Dual-line metadata labels that collapse to a single line when short.
        self.dual_line_artist = DualLineLabel(self.writer_large, label_col, safe_width - label_col)
        self.dual_line_album = DualLineLabel(self.writer_small, label_col, safe_width - label_col)
        self.dual_line_title = DualLineLabel(self.writer_small, label_col, safe_width - label_col)

        self.label_tracknumber = Label(
            self.writer_small, 0, label_col, safe_width - label_col,
            align=ALIGN_LEFT, bdcolor=False,
        )

        self.label_message = Label(
            self.writer_mini, 290, 180, 290, align=ALIGN_RIGHT, bdcolor=False,
        )

        self._last_cover_url = ""
        self._last_artist = ""
        self._last_album = ""
        self._last_title = ""
        self._last_playlist_count = 0
        self.display_manager = None

    def draw_static(self):
        """Draw the full static screen. Used once at boot."""
        self.display.fill(self.display.rgb(255, 255, 255))
        self.label_volume.show()
        self.label_status.show()
        self.album_art.show()
        self.downloader.blit_last()
        self.relayout_metadata()

    def relayout_metadata(self):
        """Clear the metadata column and redraw artist/album/title/tracknumber
        with dynamic two-line collapsing. Does not touch the cover-art area.
        """
        meta_x = self.label_col
        meta_y = self.art_row
        meta_w = self.safe_width - self.label_col + 1
        meta_h = 290 - self.art_row
        self.display.fill_rect(meta_x, meta_y, meta_w, meta_h, self.display.rgb(255, 255, 255))

        y = self.art_row
        for label in (self.dual_line_artist, self.dual_line_album, self.dual_line_title):
            label.set_row(y)
            h = label.height()
            if h:
                y += h + 12

        self.label_tracknumber.row = y
        self.dual_line_artist.show()
        self.dual_line_album.show()
        self.dual_line_title.show()
        self.label_tracknumber.show()

        if self.label_message.value():
            self.label_message.show()

    def _draw_test_pattern(self):
        """Fill the album-art placeholder with an RGB565 color test pattern."""
        art = self.album_art
        w = art.size
        rgb = self.display.rgb
        for y in range(w):
            for x in range(w):
                r = (x * 255) // (w - 1)
                g = (y * 255) // (w - 1)
                b = ((x + y) * 255) // (2 * (w - 1))
                self.display.pixel(art.col + x, art.row + y, rgb(r, g, b))

    def set_initial_boot_state(self):
        self._set_title("Jukeplayer")
        self._set_artist("Booting...")
        self._set_album("")
        self._set_net_status("WS:CON")
        self._set_player_status("BOOT")
        self.relayout_metadata()

    def update(self, state={}):
        """Update screen widgets from an AppState delta."""
        metadata_changed = False

        if VOLUME in state:
            self._set_volume(state[VOLUME])
        if PLAYER_STATUS in state:
            self._set_player_status(state[PLAYER_STATUS])
        if REPEAT_STATUS in state:
            self._set_repeat_status(state[REPEAT_STATUS])
        if NETWORK_STATUS in state:
            self._set_net_status(state[NETWORK_STATUS])
        if MUTED in state:
            self._set_mute_status(state[MUTED])

        if ARTIST in state:
            self._set_artist(state[ARTIST])
            metadata_changed = True
        if ALBUM in state:
            self._set_album(state[ALBUM], state.get(YEAR))
            metadata_changed = True
        if TITLE in state:
            self._set_title(state[TITLE])
            metadata_changed = True
        if TRACK_NUMBER in state:
            self._set_track_number(state[TRACK_NUMBER], state.get(PLAYLIST_COUNT))
        if COVER_URL in state and state[COVER_URL] != self._last_cover_url:
            self._set_cover_url(state[COVER_URL])

        if metadata_changed:
            self.relayout_metadata()


    def _set_cover_url(self, url):
        """Start a cover download only when the URL (i.e. album) actually changed."""
        if not url or url == self._last_cover_url:
            return
        self._last_cover_url = url
        self.cover_url = url
        try:
            asyncio.create_task(self._download_cover(url))
        except Exception as e:
            log.error(f"[COVER] failed to start task: {e}")

    async def _download_cover(self, url):
        ok = await self.downloader.fetch_and_blit(url)
        if ok:
            dm = getattr(self, "display_manager", None)
            if dm is not None:
                dm._schedule_refresh()
            else:
                self.display.show()

    def _set_net_status(self, status):
        status = status.upper()
        if status == "WS:OK":
            self._net_icon = "\ue308"
        elif status == "WS:CON":
            self._net_icon = "\ue63e"
        else:
            self._net_icon = "\ue648"
        self._update_status_label()

    def _set_player_status(self, status):
        status = status.upper()
        if status == "PLAY":
            self._player_icon = "\ue037"
        elif status == "STOP" or status == "BOOT":
            self._player_icon = "\ue047"
        elif status == "PAUSE":
            self._player_icon = "\ue034"
        else:
            self._player_icon = ""
        self._update_status_label()

    def _set_repeat_status(self, repeat):
        self._repeat_icon = "\ue040" if repeat else ""
        self._update_status_label()

    def _set_mute_status(self, muted):
        self._mute_icon = "\ue04f" if muted else ""
        self._update_status_label()

    def _update_status_label(self):
        self.label_status.value(self._net_icon + self._player_icon + self._repeat_icon + self._mute_icon)

    def _set_volume(self, volume):
        self.label_volume.value(f"{volume}%")

    def _set_artist(self, artist):
        self._last_artist = str(artist or "")
        self.dual_line_artist.set_text(self._last_artist)

    def _set_album(self, album, year=None):
        self._last_album = str(album or "")
        text = f"{self._last_album} ({year})" if year else self._last_album
        self.dual_line_album.set_text(text)

    def _set_title(self, title):
        self._last_title = str(title or "")
        self.dual_line_title.set_text(self._last_title)

    def _set_track_number(self, track_number=0, playlist_count=None):
        if playlist_count is None:
            playlist_count = self._last_playlist_count
        else:
            self._last_playlist_count = playlist_count
        self.label_tracknumber.value(f"{track_number} / {playlist_count}")

    def show_message(self, text, header="Message"):
        """Show a temporary overlay message."""
        # header currently unused; kept for API compatibility
        _ = header
        self.label_message.value(str(text))

    def clear_message(self):
        """Hide the temporary overlay message and restore metadata labels."""
        self.label_message.value("")

    def update_frame(self):
        """No animation; the main loop does not need to refresh continuously."""
        return False
