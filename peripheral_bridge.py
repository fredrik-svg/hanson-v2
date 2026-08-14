"""
peripheral_bridge.py — Perifer styrenhet för Hanson-Pi 5.

Körs på Hanson-Pi 5:n EFTER att agent.py flyttat till VC60. Pi:n behåller
sin roll som fysisk "kropp" — LED-ring, knapp, PIR-sensor, OLED-skärm —
men slutar köra själva ElevenLabs-konversationslogiken. Istället:

  - Exponerar HTTP-endpoints så VC60-agenten kan styra LED/OLED
    (samma mönster som led_bridge.py, fast bredare: LED + OLED).
  - Kör en WebSocket-SERVER som VC60-agenten ansluter till för att ta emot
    knapptryckningar och PIR-rörelse i realtid (låg latens, ingen polling
    över nätverk från agentens sida).

ARKITEKTUR:
    [VC60 – agent.py]  ◄──WebSocket (knapp/PIR-events)──   [Hanson-Pi – denna fil]
    [VC60 – agent.py]  ──HTTP (LED/OLED-kommandon)──►      [Hanson-Pi – denna fil]

Återanvänder EXAKT samma LEDController-klass/färgschema och OLEDDisplay-
klass som agent.py, så beteendet är identiskt med tidigare, bara flyttat.

INSTALLATION (på Hanson-Pi 5):
    Kör i SAMMA venv som agent.py redan använder (samma beroenden:
    lgpio, luma.oled eller motsvarande för display.py, pi5neo, flask,
    websockets).
    pip install flask websockets

    display.py måste ligga bredvid denna fil (importeras rakt av).

KÖRNING:
    source venv/bin/activate
    python3 peripheral_bridge.py

HTTP-ENDPOINTS (LED + OLED, anropas från VC60-agenten):
    GET  /led/idle | listening | thinking | speaking | success | error | motion
    POST /display/state      body: {"state": "listening"}
    POST /display/transcript body: {"text": "..."}
    POST /display/latency    body: {"ms": 320}
    GET  /health

WEBSOCKET (port 8766, VC60-agenten ansluter som klient):
    Skickar JSON-meddelanden när knapp trycks eller PIR triggar:
    {"type": "button_press"}
    {"type": "motion"}

Säkerhet: ingen autentisering — kör ENDAST på internt, betrott LAN.
"""

import asyncio
import json
import logging
import threading
import time

from flask import Flask, jsonify, request

log = logging.getLogger("peripheral_bridge")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ── GPIO ─────────────────────────────────────────────────────────────────
try:
    import lgpio as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    log.warning("lgpio saknas — knapp/PIR inaktiva")

BUTTON_PIN = 17
PIR_PIN    = 27

# ── OLED-display (återanvänder befintlig display.py rakt av) ──────────────
DISPLAY_AVAILABLE = False
try:
    from display import OLEDDisplay, HansonState
    DISPLAY_AVAILABLE = True
except ImportError:
    log.warning("display.py saknas eller kunde inte importeras — OLED inaktiv")

    # Minimal fallback så resten av filen fungerar utan OLED inkopplad
    class HansonState:
        IDLE = "idle"
        LISTENING = "listening"
        THINKING = "thinking"
        SPEAKING = "speaking"
        MOTION = "motion"

# ── LED (identisk logik/färgschema som agent.py:s LEDController) ──────────
LED_COUNT = 16

try:
    from pi5neo import Pi5Neo
    LED_AVAILABLE = True
except ImportError:
    LED_AVAILABLE = False
    log.warning("pi5neo saknas — LED-ring inaktiv (simuleringsläge)")


class LEDController:
    IDLE           = (0,   0,   0)
    LISTENING      = (0,   100, 255)
    AGENT_SPEAKING = (200, 0,   255)
    THINKING       = (255, 165, 0)
    SUCCESS        = (0,   255, 0)
    ERROR          = (255, 0,   0)
    ENDING         = (255, 100, 0)
    MOTION         = (0,   200, 100)

    def __init__(self):
        self.neo     = None
        self._thread = None
        self._stop   = threading.Event()
        self._setup()

    def _setup(self):
        if not LED_AVAILABLE:
            return
        try:
            self.neo = Pi5Neo('/dev/spidev0.0', LED_COUNT, 800)
            self._fill(0, 0, 0)
            log.info(f"LED Ring: {LED_COUNT} LEDs via Pi5Neo/SPI0")
            self._startup_animation()
        except Exception as e:
            log.error(f"LED-fel: {e}")
            self.neo = None

    def _startup_animation(self):
        for r, g, b in [(255, 0, 0), (0, 255, 0), (0, 0, 255)]:
            self._fill(r, g, b)
            time.sleep(0.3)
        self._fill(0, 0, 0)

    def _fill(self, r, g, b):
        if not self.neo:
            return
        try:
            self.neo.fill_strip(r, g, b)
            self.neo.update_strip()
        except Exception as e:
            log.debug(f"LED fill-fel: {e}")

    def stop_effect(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._stop.clear()

    def _run(self, target, **kwargs):
        self.stop_effect()
        self._thread = threading.Thread(target=target, kwargs=kwargs, daemon=True)
        self._thread.start()

    def pulse_once(self, color):
        r, g, b = color
        for level in [0.05, 0.2, 0.4, 0.7, 1.0, 0.7, 0.4, 0.2, 0.05]:
            self._fill(int(r * level), int(g * level), int(b * level))
            time.sleep(0.04)
        self._fill(0, 0, 0)

    def start_listening(self):
        self.stop_effect()
        self._fill(*self.LISTENING)

    def start_thinking(self):
        self._run(self._spinner_loop, color=self.THINKING)

    def start_agent_speaking(self):
        self._run(self._pulse_loop, color=self.AGENT_SPEAKING)

    def start_motion(self):
        self.stop_effect()
        self._fill(*self.MOTION)

    def _pulse_loop(self, color):
        r, g, b = color
        steps = 20
        while not self._stop.is_set():
            for i in range(steps):
                if self._stop.is_set():
                    return
                lv = i / steps
                self._fill(int(r * lv), int(g * lv), int(b * lv))
                time.sleep(0.025)
            for i in range(steps, 0, -1):
                if self._stop.is_set():
                    return
                lv = i / steps
                self._fill(int(r * lv), int(g * lv), int(b * lv))
                time.sleep(0.025)

    def _spinner_loop(self, color):
        if not self.neo:
            return
        r, g, b = color
        while not self._stop.is_set():
            for i in range(LED_COUNT):
                if self._stop.is_set():
                    return
                try:
                    self.neo.fill_strip(0, 0, 0)
                    self.neo.set_led_color(i, r, g, b)
                    self.neo.set_led_color((i - 1) % LED_COUNT, r // 3, g // 3, b // 3)
                    self.neo.set_led_color((i - 2) % LED_COUNT, r // 8, g // 8, b // 8)
                    self.neo.update_strip()
                except Exception:
                    pass
                time.sleep(0.06)


led = LEDController()

display = None
if DISPLAY_AVAILABLE:
    try:
        display = OLEDDisplay()
    except Exception as e:
        log.error(f"OLED-fel: {e}")
        display = None


# ── Knapp/PIR — polling-tråd som skickar events till anslutna WS-klienter ──
class PeripheralInputWatcher:
    """
    Pollar knapp och PIR precis som agent.py gjorde tidigare, men skickar
    events över WebSocket till VC60-agenten istället för att hantera
    sessionslogiken själv. All "vad ska hända när knappen trycks"-logik
    (starta samtal, rate-limiting osv) ligger nu på VC60-sidan — denna
    tråd bara RAPPORTERAR fysiska events, den fattar inga beslut.
    """

    def __init__(self, on_button_press, on_motion):
        self.on_button_press = on_button_press
        self.on_motion = on_motion
        self.gpio_chip = None
        self._last_button_state = True   # True = ej nedtryckt (pull-up)
        self._last_button_press = 0.0
        self._last_pir_edge = 0.0
        self._running = False
        self._setup_gpio()

    def _setup_gpio(self):
        if not GPIO_AVAILABLE:
            return
        try:
            self.gpio_chip = GPIO.gpiochip_open(4)
            GPIO.gpio_claim_input(self.gpio_chip, BUTTON_PIN, GPIO.SET_PULL_UP)
            GPIO.gpio_claim_input(self.gpio_chip, PIR_PIN, GPIO.SET_PULL_UP)
            log.info(f"GPIO: Knapp=GPIO{BUTTON_PIN}, PIR=GPIO{PIR_PIN}")
        except Exception as e:
            log.error(f"GPIO-fel: {e}")
            self.gpio_chip = None

    def _read_button(self) -> bool:
        if not self.gpio_chip:
            return False
        try:
            return GPIO.gpio_read(self.gpio_chip, BUTTON_PIN) == 0
        except Exception:
            return False

    def _read_pir(self) -> bool:
        # Aktiv-LÅG PIR-klon: vila=1, rörelse=0 (verifierat i agent.py)
        if not self.gpio_chip:
            return False
        try:
            return GPIO.gpio_read(self.gpio_chip, PIR_PIN) == 0
        except Exception:
            return False

    def start(self):
        if not self.gpio_chip:
            log.warning("Ingen GPIO tillgänglig — knapp/PIR-watcher startar inte")
            return
        self._running = True
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        DEBOUNCE_S = 1.5
        while self._running:
            try:
                now = time.time()
                pressed = self._read_button()
                if pressed and (now - self._last_button_press) > DEBOUNCE_S:
                    self._last_button_press = now
                    log.info("Knapptryckning registrerad")
                    self.on_button_press()

                motion = self._read_pir()
                if motion and (now - self._last_pir_edge) > 3.0:
                    self._last_pir_edge = now
                    self.on_motion()
            except Exception as e:
                log.debug(f"Fel i poll-loop (ofarligt): {e}")
            time.sleep(0.05)


# ── WebSocket-server (skickar events till VC60, oberoende av HTTP-delen) ──
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    log.warning("Paketet 'websockets' saknas — installera med: "
                "pip install websockets")


class EventServer:
    def __init__(self, host="0.0.0.0", port=8766):
        self.host = host
        self.port = port
        self._clients = set()
        self._loop = None

    def start(self):
        if not WEBSOCKETS_AVAILABLE:
            return
        threading.Thread(target=self._run, daemon=True).start()

    def broadcast(self, message: dict):
        if not self._loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._async_broadcast(json.dumps(message)), self._loop
            )
        except Exception as e:
            log.debug(f"WS broadcast-fel (ignoreras): {e}")

    async def _async_broadcast(self, payload):
        dead = set()
        for ws in self._clients:
            try:
                await ws.send(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def _handler(self, websocket):
        self._clients.add(websocket)
        log.info(f"Agent (VC60) ansluten till event-servern ({len(self._clients)} totalt)")
        try:
            async for _ in websocket:
                pass  # denna kanal är enkelriktad (Pi → agent); inkommande ignoreras
        except Exception:
            pass
        finally:
            self._clients.discard(websocket)
            log.info(f"Agent frånkopplad ({len(self._clients)} kvar)")

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _serve():
            async with websockets.serve(self._handler, self.host, self.port):
                await asyncio.Future()

        try:
            self._loop.run_until_complete(_serve())
        except Exception as e:
            log.error(f"Event-server kraschade: {e}")


event_server = EventServer()


def _on_button_press():
    event_server.broadcast({"type": "button_press"})


def _on_motion():
    # Kosmetisk lokal reaktion (samma som tidigare _cosmetic_wake i agent.py)
    threading.Thread(target=led.pulse_once, args=(LEDController.MOTION,), daemon=True).start()
    if display:
        try:
            display.set_state(HansonState.MOTION)
        except Exception:
            pass
    event_server.broadcast({"type": "motion"})


watcher = PeripheralInputWatcher(on_button_press=_on_button_press, on_motion=_on_motion)


# ── HTTP-server (LED + OLED-kommandon, anropas av VC60-agenten) ───────────
app = Flask(__name__)

_STATE_MAP = {
    "idle": (lambda: (led.stop_effect(), led._fill(0, 0, 0))),
    "listening": led.start_listening,
    "thinking": led.start_thinking,
    "speaking": led.start_agent_speaking,
    "motion": led.start_motion,
}


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "led_hardware": LED_AVAILABLE,
        "display_hardware": display is not None,
        "gpio_hardware": GPIO_AVAILABLE and watcher.gpio_chip is not None,
        "websocket_clients": len(event_server._clients),
    })


@app.route("/led/<state>")
def led_state(state):
    fn = _STATE_MAP.get(state)
    if not fn:
        return jsonify({"ok": False, "error": f"okänt state: {state}"}), 400
    fn()
    log.info(f"LED → {state}")
    return jsonify({"ok": True, "state": state})


@app.route("/led/success")
def led_success():
    led.stop_effect()
    threading.Thread(target=led.pulse_once, args=(led.SUCCESS,), daemon=True).start()
    return jsonify({"ok": True, "state": "success"})


@app.route("/led/error")
def led_error():
    led.stop_effect()
    threading.Thread(target=led.pulse_once, args=(led.ERROR,), daemon=True).start()
    return jsonify({"ok": True, "state": "error"})


@app.route("/display/state", methods=["POST"])
def display_state():
    if not display:
        return jsonify({"ok": False, "error": "ingen OLED ansluten"}), 200
    body = request.get_json(force=True, silent=True) or {}
    state = body.get("state", "idle")
    try:
        display.set_state(state)
    except Exception as e:
        log.debug(f"display.set_state-fel: {e}")
    return jsonify({"ok": True})


@app.route("/display/transcript", methods=["POST"])
def display_transcript():
    if not display:
        return jsonify({"ok": False, "error": "ingen OLED ansluten"}), 200
    body = request.get_json(force=True, silent=True) or {}
    text = body.get("text", "")
    try:
        display.set_transcript(text)
    except Exception as e:
        log.debug(f"display.set_transcript-fel: {e}")
    return jsonify({"ok": True})


@app.route("/display/latency", methods=["POST"])
def display_latency():
    if not display:
        return jsonify({"ok": False, "error": "ingen OLED ansluten"}), 200
    body = request.get_json(force=True, silent=True) or {}
    ms = body.get("ms", 0)
    try:
        display.set_latency(ms)
    except Exception as e:
        log.debug(f"display.set_latency-fel: {e}")
    return jsonify({"ok": True})


if __name__ == "__main__":
    log.info("Peripheral bridge startar")
    log.info(f"  HTTP (LED/OLED-kommandon):  port 5001")
    log.info(f"  WebSocket (knapp/PIR-events): port 8766")
    watcher.start()
    event_server.start()
    app.run(host="0.0.0.0", port=5001)
