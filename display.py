#!/usr/bin/env python3
"""
display.py - OLED SH1106 128x64 statusdisplay för Hanson v3

Hårdvara: DollaTek 1.3" OLED (SH1106, I2C, 4-pin)
Koppling:
  VCC → 3.3V  (Pin 1)
  GND → GND   (Pin 6)
  SCL → GPIO 3 / SCL (Pin 5)
  SDA → GPIO 2 / SDA (Pin 3)

Installation:
  pip install luma.oled pillow --break-system-packages

Aktivera I2C på Pi 5:
  sudo raspi-config → Interface Options → I2C → Yes
  sudo reboot

Verifiera adress (ska visa 3c):
  sudo i2cdetect -y 1
"""

import threading
import time
import logging
from datetime import datetime
from enum import Enum, auto

log = logging.getLogger("hanson.display")

# ── Försök importera luma.oled ─────────────────────────────────────────────────
try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import sh1106
    from luma.core.render import canvas
    from PIL import ImageFont
    DISPLAY_AVAILABLE = True
except ImportError:
    DISPLAY_AVAILABLE = False
    log.warning("luma.oled saknas – display inaktiverad. Installera: pip install luma.oled pillow")

# ── Font med svenskt teckenstöd (åäö) och enkla statusprickar ──────────────────
# DejaVu Sans följer med på praktiskt taget alla Debian/Raspberry Pi OS-
# installationer. Standardfonten i Pillow saknar både åäö och ●/○.
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

def _load_font(size: int):
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    log.warning("DejaVuSans.ttf hittades inte — åäö/●○ kommer inte visas korrekt")
    return None

if DISPLAY_AVAILABLE:
    FONT_NORMAL = _load_font(11)
    FONT_SMALL  = _load_font(9)
else:
    FONT_NORMAL = None
    FONT_SMALL  = None


# ── Tillstånd ──────────────────────────────────────────────────────────────────
class HansonState(Enum):
    IDLE      = auto()   # Väntar på knapptryck
    MOTION    = auto()   # Kosmetisk PIR-väckning (ingen session)
    LISTENING = auto()   # Konversation aktiv, lyssnar
    THINKING  = auto()   # Agenten bearbetar svar
    SPEAKING  = auto()   # Agenten pratar
    ERROR     = auto()   # Fel uppstod


STATE_LABELS = {
    HansonState.IDLE:      "Väntar på knapp",
    HansonState.MOTION:    "Hej där!",
    HansonState.LISTENING: "Lyssnar",
    HansonState.THINKING:  "Tänker...",
    HansonState.SPEAKING:  "Svarar",
    HansonState.ERROR:     "FEL",
}

STATE_ICONS = {
    HansonState.IDLE:      "[ zzz ]",
    HansonState.MOTION:    "[  !  ]",
    HansonState.LISTENING: "[ MIC ]",
    HansonState.THINKING:  "[  ?  ]",
    HansonState.SPEAKING:  "[ >>> ]",
    HansonState.ERROR:     "[ ERR ]",
}


# ══════════════════════════════════════════════════════════════════════════════
class OLEDDisplay:
    """
    Hanterar SH1106 128x64 OLED-display via I2C.
    Uppdateras i en bakgrundstråd för att inte blockera konversationslogiken.

    Layout (128x64 px):
    ┌────────────────────────────┐
    │ HANSON           15:55    │
    │ ──────────────────────── │
    │ [ MIC ]  Lyssnar          │
    │                           │
    │ ElevenLabs  ● ONLINE      │
    │ Internet    ● ONLINE      │
    └────────────────────────────┘
    """

    def __init__(self, i2c_port: int = 1, i2c_address: int = 0x3C):
        self.device          = None
        self._lock           = threading.Lock()
        self._thread         = None
        self._stop           = threading.Event()
        self._dirty          = threading.Event()

        self.state            = HansonState.IDLE
        self.last_transcript  = ""
        self.last_latency_ms  = 0
        self.elevenlabs_ok    = False
        self.internet_ok      = False

        self._anim_tick = 0

        self._setup(i2c_port, i2c_address)

    def _setup(self, port: int, address: int):
        if not DISPLAY_AVAILABLE:
            return
        try:
            serial = i2c(port=port, address=address)
            self.device = sh1106(serial, width=128, height=64, rotate=0)
            log.info(f"OLED SH1106 redo på I2C-{port} addr=0x{address:02X}")
            self._start_render_thread()
            self._show_splash()
        except Exception as e:
            log.error(f"OLED-fel: {e}")
            self.device = None

    def _start_render_thread(self):
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    # ── Publikt API ────────────────────────────────────────────────────────────
    def set_state(self, state: HansonState):
        if state is None:
            return
        if self.state != state:
            self.state = state
            self._anim_tick = 0
            self._dirty.set()

    def set_transcript(self, text: str):
        self.last_transcript = text[:22] + "…" if len(text) > 22 else text
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
                self.device.cleanup()
            except Exception:
                pass

    # ── Rendering ──────────────────────────────────────────────────────────────
    def _render_loop(self):
        while not self._stop.is_set():
            self._dirty.wait(timeout=1.0)
            self._dirty.clear()
            if self._stop.is_set():
                break
            self._anim_tick += 1
            self._draw()

    def _draw(self):
        if not self.device:
            return

        now = datetime.now()
        time_str = now.strftime("%H:%M")

        dots = "." * ((self._anim_tick % 3) + 1) if self.state == HansonState.THINKING else ""
        icon   = STATE_ICONS.get(self.state, "")
        status = STATE_LABELS.get(self.state, "")
        if self.state == HansonState.THINKING:
            status = "Tänker" + dots

        el_dot  = "●" if self.elevenlabs_ok else "○"
        net_dot = "●" if self.internet_ok   else "○"
        latency = f"{self.last_latency_ms}ms" if self.last_latency_ms else "---"

        with self._lock:
            try:
                with canvas(self.device) as draw:
                    draw.text((0, 0),   "HANSON",    fill="white", font=FONT_NORMAL)
                    draw.text((90, 0),  time_str,    fill="white", font=FONT_NORMAL)
                    draw.line([(0, 11), (127, 11)], fill="white")

                    draw.text((0, 14),  icon,        fill="white", font=FONT_NORMAL)
                    draw.text((52, 14), status,      fill="white", font=FONT_NORMAL)

                    if self.last_transcript and self.state in (
                        HansonState.LISTENING, HansonState.THINKING, HansonState.SPEAKING
                    ):
                        draw.text((0, 26), self.last_transcript, fill="white", font=FONT_NORMAL)

                    draw.line([(0, 37), (127, 37)], fill="white")
                    draw.text((0, 40),  f"ElevenLabs  {el_dot}", fill="white", font=FONT_SMALL)
                    draw.text((0, 50),  f"Internet    {net_dot}", fill="white", font=FONT_SMALL)
                    draw.text((80, 50), latency,     fill="white", font=FONT_SMALL)

            except Exception as e:
                log.debug(f"Ritfel: {e}")

    def _show_splash(self):
        if not self.device:
            return
        try:
            with canvas(self.device) as draw:
                draw.text((20, 10), "HANSON v3",   fill="white", font=FONT_NORMAL)
                draw.text((10, 28), "Startar upp...", fill="white", font=FONT_NORMAL)
                draw.line([(0, 22), (127, 22)],     fill="white")
            time.sleep(1.5)
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
    display = OLEDDisplay()

    if not display.device:
        print("\nIngen display hittades. Kontrollera:")
        print("  1. I2C aktiverat: sudo raspi-config → Interface Options → I2C")
        print("  2. Koppling: VCC→Pin1, GND→Pin6, SCL→Pin5, SDA→Pin3")
        print("  3. sudo i2cdetect -y 1  (bör visa 3c)")
        exit(1)

    print("Display-test. Ctrl+C för att avsluta.\n")
    display.check_connectivity()

    test_cases = [
        (HansonState.IDLE,      "",                 0),
        (HansonState.MOTION,    "",                 0),
        (HansonState.LISTENING, "Berätta mer...",   0),
        (HansonState.THINKING,  "Vad är klockan?", 312),
        (HansonState.SPEAKING,  "Vad är klockan?", 312),
        (HansonState.ERROR,     "",                 0),
        (HansonState.IDLE,      "",                 0),
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
