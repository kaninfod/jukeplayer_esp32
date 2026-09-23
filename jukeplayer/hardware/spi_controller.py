# jukeplayer/hardware/spi_controller.py — SPI bus manager (2026-09-22)
#
# Owns the SPI peripheral shared between multiple consumers (the display,
# the NFC reader). The bus configuration persists between operations:
# acquire at your speed, use the bus, release. If the bus is already at
# your speed, no reconfiguration happens. The lock ensures mutual
# exclusion — first come, first served, no discard, no priority.
#
# The consumers are generic: they call acquire(their_baudrate) and use
# .spi for the transfers. The controller has no knowledge of the consumers.
import asyncio
from machine import Pin, SPI


class SPIController:
    def __init__(self, config):
        cfg = config.get("spi", {})
        self._unit = int(cfg.get("spi_unit", 1))
        self._polarity = int(cfg.get("polarity", 0))
        self._phase = int(cfg.get("phase", 0))
        self._sck = int(cfg.get("sck"))
        self._mosi = int(cfg.get("mosi"))
        self._miso = int(cfg.get("miso")) if cfg.get("miso") is not None else None

        self._spi = None
        self._baudrate = None
        self._lock = asyncio.Lock()

    def _ensure(self, baudrate):
        """(Re)configure the peripheral only if the speed changed."""
        if self._spi is None:
            self._spi = SPI(self._unit, baudrate=baudrate,
                            polarity=self._polarity, phase=self._phase,
                            sck=Pin(self._sck), mosi=Pin(self._mosi),
                            miso=Pin(self._miso))
            self._baudrate = baudrate
        elif self._baudrate != baudrate:
            self._spi.init(baudrate=baudrate,
                           polarity=self._polarity, phase=self._phase)
            self._baudrate = baudrate

    async def acquire(self, baudrate):
        """Acquire the bus at the given baudrate. No-op reconfiguration if
        already at this speed. Waits if another consumer holds the bus."""
        await self._lock.acquire()
        self._ensure(baudrate)

    def release(self):
        """Release the bus. The configuration persists — the next consumer's
        acquire() reconfigures only if their speed differs."""
        self._lock.release()

    @property
    def spi(self):
        """The raw SPI peripheral for the current holder's transfers."""
        return self._spi
