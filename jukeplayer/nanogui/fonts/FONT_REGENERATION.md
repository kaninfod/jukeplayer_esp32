FONT REGENERATION — jukeplayer nano-gui fonts
=============================================

Why this exists
---------------
The original geist fonts were generated WITHOUT a character-set argument, so
font_to_py used its default: ASCII 32-126 inclusive. Any character outside
that set (’ U+2019, ø U+00F8, æ, å …) was rendered as "?" on the display
(both ST7735R and ILI9488 use these font modules via the nano-gui Writer).

Fix (2026-09-07): regenerated with an explicit charset file.

The charset
-----------
charset.txt contains 199 characters:
  - ASCII printable 32-126 (the previous default set, kept)
  - Latin-1 supplement 160-255 (ÆØÅ, ß, °, µ, ×, ¿ … — Danish/German/French
    metadata; NBSP 160 and soft hyphen 173 are auto-filtered as non-printable)
  - typographic extras: ‘ ’ “ ” – — … €

font_to_py filters to printable + Private-Use-Area (0xE000-0xF8FF, needed for
Material icon codepoints), dedupes, and excludes the error char ("?") itself.
Missing characters in text render as "?" — with this charset that should only
happen for exotic scripts (Cyrillic, CJK …).

The regeneration commands (run from jukeplayer/nanogui/fonts/, or adjust paths):

  FT=~/projects/esp32_nanogui_fonts/.venv/bin/font_to_py
  TTF=~/projects/esp32_nanogui_fonts/geist-font-1.8.0/fonts/GeistMono/ttf/GeistMono-Bold.ttf

  $FT -f -x $TTF 10 geistmonobold10.py -k charset.txt
  $FT -f -x $TTF 14 geistmonobold14.py -k charset.txt
  $FT -x -f $TTF 18 geistmonobold18.py -k charset.txt
  $FT -x   $TTF 24 geistmonobold24.py -k charset.txt

IMPORTANT: preserve each font's original flags (-f fixed-width, -x x-map) —
they define the bitmap layout. Only the charset argument is new (-k charset.txt;
without it the tool defaults to ASCII 32-126 and every non-ASCII character
renders as "?").

Notes:
- material_subset.py (Material icons) needs no regeneration — its -c icon set
  works, and the PUA range passes font_to_py's filter.
- geistmonobold12/16, geistmonomed12, geistmonoreg12 in ~/projects/esp32_nanogui_fonts/
  are NOT used by the app (grep the managers to confirm) — regenerate only if
  ever adopted.
- The ~/projects/esp32_nanogui_fonts/ directory is currently owned by UID 777
  (archive extraction side effect) and read-only for other users — hence the
  charset/doc live here in the repo. To write there again:
  sudo chown -R $(whoami) ~/projects/esp32_nanogui_fonts

Deploy after regeneration: copy the changed font modules to both devices
(jukeplayer/nanogui/fonts/*.py) and soft reset. Verify with a track title
containing ’ or ø (e.g. "Jeg havde en drøm").