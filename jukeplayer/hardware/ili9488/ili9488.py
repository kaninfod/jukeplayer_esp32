# ILI9488 RGB565 framebuffer driver for SPI.
#
# Subclasses framebuf.FrameBuffer in RGB565 mode (2 bytes/pixel) and expands
# each row on the fly to the ILI9488's required 18-bit RGB666 SPI format
# (3 bytes/pixel). This yields ~65K displayable colors while keeping the
# same nano-gui / DisplayManager contract as the original 4-bit LUT driver.
#
# Based on Peter Hinch's nano-gui ILI9488 driver; retaining the same init
# sequence and SPI transaction shape that are already proven on this hardware.

from time import sleep_ms
import gc
import framebuf
import asyncio
from jukeplayer.hardware.boolpalette import BoolPalette
from jukeplayer.core.logger import log


# Convert a row of RGB565 pixels (2 bytes/pixel) to RGB666 bytes (3 bytes/pixel).
# MADCTL is configured for RGB color order, so transmit R, G, B.
@micropython.viper
def _lcopy_rgb565(dest: ptr8, source: ptr16, src_offset: int, pixels: int):
    s: int = src_offset
    d: int = 0
    while pixels:
        pixels -= 1
        c: uint = source[s]
        s += 1
        # RGB565 layout: RRRRRGGG GGGBBBBB
        dest[d] = (c & 0xF800) >> 8   # R
        d += 1
        dest[d] = (c & 0x07E0) >> 3   # G
        d += 1
        dest[d] = (c & 0x001F) << 3   # B
        d += 1


class ILI9488_RGB565(framebuf.FrameBuffer):

    # Convert r, g, b (0-255) to a 16-bit RGB565 colour value.
    # This is used directly by the FrameBuffer and by CWriter when no LUT exists.
    @staticmethod
    def rgb(r, g, b):
        return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

    def __init__(
        self,
        spi,
        cs,
        dc,
        rst,
        height=320,
        width=480,
        usd=True,
        mirror=False,
        init_spi=False,
        lines_per_write=4,
    ):
        self._spi = spi
        self._cs = cs
        self._dc = dc
        self._rst = rst
        self.lock_mode = False
        self.height = height
        self.width = width
        self._spi_init = init_spi
        self.mode = framebuf.RGB565
        self.palette = BoolPalette(self.mode)

        if (self.height % lines_per_write) != 0:
            raise ValueError("lines_per_write invalid")
        self._lines_per_write = lines_per_write

        gc.collect()
        # RGB565: 2 bytes per pixel.
        buf = bytearray(height * width * 2)
        self.mvb = memoryview(buf)
        super().__init__(buf, width, height, self.mode)

        # Temporary line buffer: up to lines_per_write rows, 3 bytes per pixel.
        self._linebuf = bytearray(self._lines_per_write * self.width * 3)

        # Hardware reset.
        self._rst(0)
        sleep_ms(50)
        self._rst(1)
        sleep_ms(50)
        if self._spi_init:
            self._spi_init(spi)
        self._lock = asyncio.Lock()

        # Minimal, proven ILI9488 init sequence.
        self._wcmd(b"\x01")  # SWRESET
        sleep_ms(100)
        self._wcmd(b"\x11")  # Sleep out
        sleep_ms(20)
        self._wcd(b"\x3a", b"\x66")  # 18-bit RGB666 interface pixel format

        self._wcd(b"\x2a", int.to_bytes(self.width - 1, 4, "big"))
        self._wcd(b"\x2b", int.to_bytes(self.height - 1, 4, "big"))

        if self.width > self.height:
            madctl = 0xE8 if usd else 0x28
        else:
            madctl = 0x48 if usd else 0x88
        if mirror:
            madctl ^= 0x80
        log.info(f"[ILI9488] MADCTL=0x{madctl:02X} usd={usd} mirror={mirror} width={self.width} height={self.height}")
        self._wcd(b"\x36", madctl.to_bytes(1, "big"))

        self._wcmd(b"\x11")  # Sleep out
        self._wcmd(b"\x29")  # Display on

    def _wcmd(self, command):
        self._dc(0)
        self._cs(0)
        self._spi.write(command)
        self._cs(1)

    def _wcd(self, command, data):
        self._dc(0)
        self._cs(0)
        self._spi.write(command)
        self._cs(1)
        self._dc(1)
        self._cs(0)
        self._spi.write(data)
        self._cs(1)

    def show(self):
        """Blocking refresh: convert the RGB565 framebuffer to RGB666 and send it."""
        lb = self._linebuf
        buf = self.mvb
        if self._spi_init:
            self._spi_init(self._spi)
        self._wcmd(b"\x2c")  # WRITE_RAM
        self._dc(1)
        self._cs(0)
        width = self.width
        pixels_per_block = self._lines_per_write * width
        spi_write = self._spi.write
        lcopy = _lcopy_rgb565
        for start in range(0, width * self.height, pixels_per_block):
            lcopy(lb, buf, start, pixels_per_block)
            spi_write(lb)
        self._cs(1)

    def short_lock(self, v=None):
        if v is not None:
            self.lock_mode = v
        return self.lock_mode

    async def do_refresh(self, split=4, elock=None):
        """Async refresh yielding between segments."""
        if elock is None:
            elock = asyncio.Lock()
        async with self._lock:
            lines, mod = divmod(self.height, split)
            if mod:
                raise ValueError("Invalid do_refresh arg 'split'")
            if lines % self._lines_per_write != 0:
                raise ValueError(
                    "Invalid do_refresh arg 'split' for lines_per_write of %d"
                    % (self._lines_per_write)
                )

            lb = self._linebuf
            buf = self.mvb
            width = self.width
            pixels_per_block = self._lines_per_write * width
            spi_write = self._spi.write
            lcopy = _lcopy_rgb565

            self._wcmd(b"\x2c")
            self._dc(1)
            line = 0
            for _ in range(split):
                async with elock:
                    if self._spi_init:
                        self._spi_init(self._spi)
                    self._cs(0)
                    for start in range(
                        width * line, width * (line + lines), pixels_per_block
                    ):
                        lcopy(lb, buf, start, pixels_per_block)
                        spi_write(lb)
                    line += lines
                    self._cs(1)
                await asyncio.sleep_ms(0)
