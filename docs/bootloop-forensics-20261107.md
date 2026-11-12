# Bootloop forensics 2026-11-07 — klangmeister (in progress)

Status: instrumented and on watch. Death class pending the next
nightly ~03:0x wifi event (tonight / morning of 2026-11-08).

## The case in one paragraph

The klangmeister (ESP32-S3/green board in the radio chassis, device_id
`gvaudio`, IP .181) cold-boots **every night at ~03:0x** (DHCPDISCOVER in
the router log, new backend WS UUID, ~30 consecutive nights Oct 13 → Nov 7).
The WS session churns at that moment while the device's app process DIES
(it never logs `[RECONNECT]`, which the deployed tree does log — verified
byte-identical to git 63c4259). The testbench on the same event reconnects
in 1 s without rebooting (verified live 2026-11-07 03:01:37). On 2026-11-06
the nightly event escalated: the device rode a **20-cycle power/reboot flap
storm 03:01:32→03:08:07** (18 s cadence), one boot survived with a corrupted
heap (30 s cadence `'[JukeBoxApp] object has no attribute
'_log_memory_usage'` — `_log_memory_usage` has existed since the first
milestone commit; the class was damaged in RAM, not on flash), and the
user's 08:09:59 NFC push (read OK: al-1386, backend played the album)
killed that boot 10 s later. Thereafter: panic/WDT-class bootloop (the
boot.py stay-down gate engaged after exactly 4 DHCP cycles at 08:11→08:12),
then silence — every boot dying before the syslog replay could flush.

## What has been ruled out

- Backend: no `device_reset`/`config_set` activity in Loki (Nov 4–7);
  the backend itself never restarts nightly (testbench WS would churn with it).
- Home Assistant: owner confirms no automation touching the jukebox.
- Router box: never reboots (2-min ethtool crond + PID continuity Nov 4–7);
  hostapd shows only manual daytime reloads (Oct 13/15/18/25).
- Router wireless as a *box-level* event: testbench unaffected on clean nights
  (but note: testbench was unpowered overnight on those nights — Nov 4/5/6
  mornings show 0 testbench lines 02:40–03:20 vs 240 on the night it stayed up).
- Device code: deployed tree verified **byte-identical to git 63c4259**
  (`app.py 12288/917996`, `boot.py 7822/610229`); no timed trigger exists in
  any committed tree (`machine.reset()` only: crash path, two-stage stop
  long-press, WS `device_reset`).
- The nightly trigger is NOT a lease renewal: the device sends a fresh
  `DHCPDISCOVER` (cold boot marker), renewals are plain `DHCPREQUEST`
  (e.g. 02:50 nightly = T1 of the lease).
- Board + firmware: healthy — direct devboard power bench test 2026-11-07
  (30+ min stable, WS + NFC 0x92 + telemetry, and the *full* Nov-6 killer
  sequence replayed cleanly on PCB power at 12:39:51: card push → al-1386 →
  album play → cover fetch → 3× full-frame pushes, −28 KB net memory).

## Where the case stands (2026-11-07 14:00)

- Deployed (serial, verified): `boot.py` 8537/668294 + `jukeplayer/app.py`
  12897/964939 (staged boot + `[CRASH]` reset-class forensics + tree check)
  and both display managers with the `[TFT]/[ILI9488] idle tick` DEBUG
  spam removed (ili9488 31807/2440441, st7735r 16300/1265205).
- `logging.level: DEBUG` (owner edit 12:00) and
  `logging.file.enabled: true` (on-device flash capture — the only observer
  that survives a pre-WiFi death). Live config from the backend kiosk API:
  WDT **enabled 8 s**, tft baudrate 24 MHz — the preserved
  `config_files/S3/config.json` is stale (was showing watchdog off, 4 MHz,
  backend .149). The WDT was NOT changed by the owner today.
- Power cables rearranged inside the chassis: 220 V ≥30 cm from the PCB,
  no 5 V wiring within 10 cm of the radio.
- Chassis reinstall done; device streaming, registration 5a73037d, MEM flat
  ~7.63 MB.

## Known observability gap (queued, Tier-2)

The syslog boot replay loses its **head** on some boots (e.g. the 14:19
post-deploy boot delivered from `[NTP]` onward): the pre-WiFi segment
(`[CFG]`, `[CRASH]` forensics, stages 1–2) rides the first UDP packets
emitted right after association and can vanish. `wifi_manager` logs only
~3 lines, so queue-cap eviction is ruled out; this is UDP head-loss at
radio settle. Consequences: the `[CRASH] reset reason` line can be missing
from Loki after a death. Mitigations in place: the on-device
`jukeplayer.log` (flash, written at `mark_syslog_online`, no network) holds
the full boot window; and the morning after a death the device is
restartable into a boot whose replay includes the *previous* run's
classification only if that classification was flushed before
syslog-online — hence the file is authoritative. **Queued fix (do not
drive-by): settle delay (~150 ms) before the first syslog flush at
`mark_syslog_online`.**

## Next steps

1. Tonight: leave the klangmeister powered in the chassis. Watch the
   ~03:0x event live (backend churn + DHCPDISCOVER cadence; the DEBUG
   stream will carry the death trail if the app survives long enough).
2. Tomorrow morning: read the class from
   `[CRASH] reset reason: …` (Loki) and `jukeplayer.log` (device). Classes:
   PWRON(1) → power dip tied to the wifi event (rail); HARD(2)/WDT(3) →
   software panic/watchdog on the drop (then look hard at the WLAN driver
   + the webrepl listener on this build).
3. The AP-side trigger hunt continues independently: which AP does the
   radio anchor to, does it (or anything in the chain — a mesh node,
   timer plug, the radio's own standby schedule) cycle ~03:0x nightly.
