#!/usr/bin/env python3
"""
display.py - ST7735 1.77" TFT färgdisplay för Hanson v3
AZ-Delivery 1.77 Zoll SPI TFT-Display (160x128px, ST7735)

Koppling (SPI0, CE1 = /dev/spidev0.1):
  VCC → 3.3V       (Pin 1)
  GND → GND        (Pin 9)
  SCK → GPIO 11    (Pin 23)
  SDA → GPIO 10    (Pin 19)
  CS  → GPIO 7/CE1 (Pin 26)
  DC  → GPIO 23    (Pin 16)
  RES → GPIO 24    (Pin 18)
  LED → 3.3V       (Pin 1)

Installation:
  pip install luma.lcd pillow --break-system-packages

Aktivera SPI på Pi 5:
  sudo raspi-config → Interface Options → SPI → Yes
  sudo reboot

Testa:
  sudo python3 display.py
"""

import threading
import time
import logging
from datetime import datetime
from enum import Enum, auto

log = logging.getLogger("hanson.display")

# ── Färgpalett ─────────────────────────────────────────────────────────────────
BLACK   = "black"
WHITE   = "white"
GREEN   = "#00FF7F"    # Online / OK
RED     = "#FF3333"    # Fel / offline
ORANGE  = "#FF8C00"    # Tänker
CYAN    = "#00BFFF"    # Lyssnar
PURPLE  = "#CC44FF"    # Agenten pratar
YELLOW  = "#FFD700"    # Rörelse
GRAY    = "#666666"    # Inaktiv text
BGDARK  = "#0A0A0A"    # Nästan svart bakgrund

# ── Försök importera luma.lcd ──────────────────────────────────────────────────
try:
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7789
    from luma.core.render import canvas
    from PIL import ImageFont, ImageDraw, Image
    DISPLAY_AVAILABLE = True
except ImportError:
    DISPLAY_AVAILABLE = False
    log.warning("luma.lcd saknas. Installera: pip install luma.lcd pillow --break-system-packages")


# ══════════════════════════════════════════════════════════════════════════════
class HansonState(Enum):
    IDLE          = auto()
    MOTION        = auto()
    LISTENING     = auto()
    THINKING      = auto()
    SPEAKING      = auto()
    ERROR         = auto()


# Färg per tillstånd (används för statustext och accent-linje)
STATE_COLOR = {
    HansonState.IDLE:      GRAY,
    HansonState.MOTION:    YELLOW,
    HansonState.LISTENING: CYAN,
    HansonState.THINKING:  ORANGE,
    HansonState.SPEAKING:  PURPLE,
    HansonState.ERROR:     RED,
}

STATE_LABEL = {
    HansonState.IDLE:      "Väntar",
    HansonState.MOTION:    "Rörelse!",
    HansonState.LISTENING: "Lyssnar",
    HansonState.THINKING:  "Tänker",
    HansonState.SPEAKING:  "Svarar",
    HansonState.ERROR:     "FEL",
}

STATE_ICON = {
    HansonState.IDLE:      "zzz",
    HansonState.MOTION:    "!!!",
    HansonState.LISTENING: "MIC",
    HansonState.THINKING:  " ? ",
    HansonState.SPEAKING:  ">>>",
    HansonState.ERROR:     "ERR",
}


# ══════════════════════════════════════════════════════════════════════════════
class TFTDisplay:
    """
    Hanterar AZ-Delivery 1.77" ST7735 (160x128px) via luma.lcd.

    Layout (160x128 px, liggande = portrait 128wide x 160tall):
    Skärmen monteras stående → 128px bred, 160px hög

    ┌────────────────────┐  y=0
    │  HANSON   10:42   │  Rubrik
    ├────────────────────┤  y=20  accent-linje (färgad per state)
    │  [MIC]  Lyssnar   │  y=26  Ikon + statustext
    │                   │
    │  "användaren sa"  │  y=55  Transkription (färgad)
    │                   │
    ├────────────────────┤  y=95  separator
    │ ElevenLabs  ● ON  │  y=100 tjänststatus
    │ Internet    ● ON  │  y=112 nätverksstatus
    │           312ms   │  y=124 latens
    └────────────────────┘  y=160
    """

    W = 128
    H = 160

    def __init__(self,
                 spi_port: int = 0,
                 spi_device: int = 1,
                 gpio_dc: int = 23,
                 gpio_rst: int = 24):
        self.device          = None
        self._lock           = threading.Lock()
        self._thread         = None
        self._stop           = threading.Event()
        self._dirty          = threading.Event()
        self._anim_tick      = 0

        # State
        self.state           = HansonState.IDLE
        self.last_transcript = ""
        self.last_latency_ms = 0
        self.elevenlabs_ok   = False
        self.internet_ok     = False

        self._setup(spi_port, spi_device, gpio_dc, gpio_rst)

    # ── Setup ──────────────────────────────────────────────────────────────────
    def _setup(self, spi_port, spi_device, gpio_dc, gpio_rst):
        if not DISPLAY_AVAILABLE:
            return
        try:
            serial = spi(
                port=spi_port,
                device=spi_device,
                gpio_DC=gpio_dc,
                gpio_RST=gpio_rst,
                bus_speed_hz=32_000_000,   # 32MHz — snabbt men stabilt
            )
            self.device = st7789(
                serial,
                width=self.W,
                height=self.H,
                bgr=False,
                rotate=0,
            )
            log.info(f"ST7735 TFT redo: SPI{spi_port}.{spi_device}, DC=GPIO{gpio_dc}, RST=GPIO{gpio_rst}")
            self._start_render_thread()
            self._show_splash()
        except Exception as e:
            log.error(f"TFT-fel: {e}")
            self.device = None

    def _start_render_thread(self):
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    # ── Publikt API ────────────────────────────────────────────────────────────
    def set_state(self, state: HansonState):
        if self.state != state:
            self.state = state
            self._anim_tick = 0
            self._dirty.set()

    def set_transcript(self, text: str):
        # Dela upp i två rader om texten är lång (max ~16 tecken per rad vid liten font)
        if len(text) > 18:
            self.last_transcript = text[:18] + "…"
        else:
            self.last_transcript = text
        self._dirty.set()

    def set_latency(self, ms: int):
        self.last_latency_ms = ms
        self._dirty.set()

    def set_connection_status(self, elevenlabs: bool, internet: bool):
        self.elevenlabs_ok = elevenlabs
        self.internet_ok   = internet
        self._dirty.set()

    def cleanup(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        if self.device:
            try:
                # Svart skärm vid avstängning
                with canvas(self.device) as draw:
                    draw.rectangle([(0,0),(self.W, self.H)], fill=BLACK)
                self.device.cleanup()
            except Exception:
                pass

    # ── Rendering ──────────────────────────────────────────────────────────────
    def _render_loop(self):
        while not self._stop.is_set():
            triggered = self._dirty.wait(timeout=1.0)
            self._dirty.clear()
            if self._stop.is_set():
                break
            self._anim_tick += 1
            self._draw()

    def _draw(self):
        if not self.device:
            return

        now        = datetime.now()
        time_str   = now.strftime("%H:%M")
        state      = self.state
        color      = STATE_COLOR.get(state, GRAY)
        label      = STATE_LABEL.get(state, "")
        icon       = STATE_ICON.get(state, "   ")

        # Animerade prickar för THINKING
        if state == HansonState.THINKING:
            dots  = "." * ((self._anim_tick % 3) + 1)
            label = "Tänker" + dots

        el_dot   = ("●", GREEN) if self.elevenlabs_ok  else ("○", RED)
        net_dot  = ("●", GREEN) if self.internet_ok    else ("○", RED)
        latency  = f"{self.last_latency_ms}ms" if self.last_latency_ms else "---"

        with self._lock:
            try:
                with canvas(self.device) as draw:
                    # ── Bakgrund ───────────────────────────────────────────
                    draw.rectangle([(0, 0), (self.W, self.H)], fill=BGDARK)

                    # ── Rubrik: HANSON + klocka ────────────────────────────
                    draw.text((4, 4),      "HANSON", fill=WHITE)
                    draw.text((88, 4),     time_str,  fill=GRAY)

                    # ── Accent-linje (färg per state) ──────────────────────
                    draw.rectangle([(0, 18), (self.W, 20)], fill=color)

                    # ── Ikon-badge ─────────────────────────────────────────
                    draw.rectangle([(4, 26), (36, 44)], fill=color, outline=color)
                    draw.text((8, 28), icon, fill=BLACK)

                    # ── Statustext ─────────────────────────────────────────
                    draw.text((42, 28), label, fill=color)

                    # ── Transkription ──────────────────────────────────────
                    if self.last_transcript and state in (
                        HansonState.LISTENING,
                        HansonState.THINKING,
                        HansonState.SPEAKING,
                    ):
                        draw.text((4, 52), self.last_transcript, fill=WHITE)

                    # ── Separator ──────────────────────────────────────────
                    draw.rectangle([(0, 90), (self.W, 91)], fill="#222222")

                    # ── ElevenLabs-status ──────────────────────────────────
                    draw.text((4, 95),   "ElevenLabs", fill=GRAY)
                    draw.text((104, 95), el_dot[0],    fill=el_dot[1])

                    # ── Internet-status ────────────────────────────────────
                    draw.text((4, 109),  "Internet",   fill=GRAY)
                    draw.text((104, 109), net_dot[0],  fill=net_dot[1])

                    # ── Latens ─────────────────────────────────────────────
                    draw.text((4, 123),  "Latens",     fill=GRAY)
                    draw.text((88, 123), latency,      fill=WHITE if self.last_latency_ms else GRAY)

            except Exception as e:
                log.debug(f"Ritfel: {e}")

    def _show_splash(self):
        if not self.device:
            return
        try:
            with canvas(self.device) as draw:
                draw.rectangle([(0,0),(self.W, self.H)], fill=BGDARK)
                # Stor rubrik
                draw.text((14, 40),  "HANSON",       fill=WHITE)
                draw.text((22, 60),  "v3",           fill=CYAN)
                draw.rectangle([(0, 78),(self.W, 80)], fill=CYAN)
                draw.text((8, 86),   "Startar upp…", fill=GRAY)
            time.sleep(2.0)
        except Exception:
            pass

    # ── Nätverkskontroll ──────────────────────────────────────────────────────
    def check_connectivity(self):
        import socket
        try:
            socket.setdefaulttimeout(2)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("8.8.8.8", 53))
            s.close()
            internet_ok = True
        except Exception:
            internet_ok = False

        try:
            import urllib.request
            urllib.request.urlopen("https://api.elevenlabs.io", timeout=3)
            el_ok = True
        except Exception:
            el_ok = internet_ok

        self.set_connection_status(el_ok, internet_ok)
        return el_ok, internet_ok


# ══════════════════════════════════════════════════════════════════════════════
# Standalone-test: sudo python3 display.py
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

    display = TFTDisplay()

    if not display.device:
        print("\nIngen display hittades. Kontrollera:")
        print("  1. SPI aktiverat: sudo raspi-config → Interface Options → SPI")
        print("  2. Koppling: CS→Pin26(CE1), DC→Pin16, RST→Pin18")
        print("  3. Bibliotek: pip install luma.lcd pillow --break-system-packages")
        exit(1)

    print("Display-test. Ctrl+C för att avsluta.\n")
    display.check_connectivity()

    test_cases = [
        (HansonState.IDLE,      "",                    0),
        (HansonState.MOTION,    "",                    0),
        (HansonState.LISTENING, "Berätta mer...",      0),
        (HansonState.THINKING,  "Vad är klockan?",   312),
        (HansonState.SPEAKING,  "Vad är klockan?",   312),
        (HansonState.ERROR,     "",                    0),
        (HansonState.IDLE,      "",                    0),
    ]

    try:
        for state, transcript, latency in test_cases:
            print(f"  → {state.name}")
            display.set_state(state)
            display.set_transcript(transcript)
            if latency:
                display.set_latency(latency)
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        display.cleanup()
        print("Avslutat.")
