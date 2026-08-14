"""
remote_peripherals.py — Nätverksklienter mot peripheral_bridge.py.

Används av agent.py NÄR AGENTEN KÖRS PÅ EN MASKIN UTAN EGEN GPIO (t.ex.
VC60), och den fysiska knappen/LED-ringen/OLED-skärmen/PIR-sensorn sitter
kvar på Hanson-Pi 5:n som kör peripheral_bridge.py.

Erbjuder samma GRÄNSSNITT (metodnamn) som de lokala LEDController- och
OLEDDisplay-klasserna, så agent.py:s anrop (self.led.start_listening(),
self.display.set_transcript(...) osv) fungerar oförändrade — bara att de
nu går över nätverk till Hanson-Pi:n istället för lokal GPIO.

Designprincip: ALDRIG blockera eller krascha agenten om Pi:n/nätverket är
otillgängligt. Alla anrop är "best effort" med kort timeout — LED/OLED är
kosmetiska, ett samtal ska aldrig hänga eller misslyckas för att en
lampuppdatering tog för lång tid eller gick fel.

Konfiguration: sätt PERIPHERAL_BRIDGE_HOST i .env, t.ex.
    PERIPHERAL_BRIDGE_HOST=192.168.1.60
(IP-adressen till Hanson-Pi 5:n som kör peripheral_bridge.py)
"""

import asyncio
import json
import logging
import threading
import time

import requests

log = logging.getLogger("hanson.remote_peripherals")

HTTP_TIMEOUT = 0.8   # sekunder — kort, LED/OLED ska ALDRIG fördröja ett samtal


class RemoteLEDController:
    """Drop-in-ersättning för LEDController. Samma metodnamn, men skickar
    HTTP-anrop till peripheral_bridge.py istället för att styra GPIO lokalt."""

    # Behålls för kod som refererar LEDController.SUCCESS etc direkt
    IDLE           = (0,   0,   0)
    LISTENING      = (0,   100, 255)
    AGENT_SPEAKING = (200, 0,   255)
    THINKING       = (255, 165, 0)
    SUCCESS        = (0,   255, 0)
    ERROR          = (255, 0,   0)
    ENDING         = (255, 100, 0)
    MOTION         = (0,   200, 100)

    def __init__(self, bridge_host: str, bridge_port: int = 5001):
        self.base_url = f"http://{bridge_host}:{bridge_port}"
        self._session = requests.Session()

    def _get(self, path: str):
        try:
            self._session.get(f"{self.base_url}{path}", timeout=HTTP_TIMEOUT)
        except Exception as e:
            log.debug(f"LED-brygga onåbar ({path}): {e}")

    def _fire_and_forget(self, path: str):
        # Skickas i egen tråd så en trög/död brygga aldrig blockerar agentens
        # huvudflöde (t.ex. mitt i ett tal-callback).
        threading.Thread(target=self._get, args=(path,), daemon=True).start()

    def start_listening(self):
        self._fire_and_forget("/led/listening")

    def start_thinking(self):
        self._fire_and_forget("/led/thinking")

    def start_agent_speaking(self):
        self._fire_and_forget("/led/speaking")

    def start_motion(self):
        self._fire_and_forget("/led/motion")

    def stop_effect(self):
        self._fire_and_forget("/led/idle")

    def pulse_once(self, color):
        # Bryggan känner bara till success/error/motion/ending som fördefinierade
        # pulser (den ser inte den generiska (r,g,b)-tupeln agent.py skickar).
        # Mappa de kända ENDING/SUCCESS/ERROR-tuplerna till rätt endpoint;
        # okänd färg faller tillbaka till "motion" (neutral kort puls).
        if color == self.SUCCESS:
            self._fire_and_forget("/led/success")
        elif color == self.ERROR:
            self._fire_and_forget("/led/error")
        else:
            self._fire_and_forget("/led/motion")

    def cleanup(self):
        self._fire_and_forget("/led/idle")

    # Client-tool-metoderna (set_led_color/run_led_animation) som registreras
    # mot ElevenLabs används inte i fjärrläget — bryggan exponerar bara de
    # fördefinierade tillstånden. Ge no-op-implementationer så registreringen
    # i agent.py inte kraschar om den koden körs oförändrad.
    def tool_set_color(self, *args, **kwargs):
        log.debug("tool_set_color anropad i fjärrläge — ingen effekt (ej implementerat i bryggan)")
        return {"status": "ignored_remote_mode"}

    def tool_run_animation(self, *args, **kwargs):
        log.debug("tool_run_animation anropad i fjärrläge — ingen effekt (ej implementerat i bryggan)")
        return {"status": "ignored_remote_mode"}


class RemoteDisplay:
    """Drop-in-ersättning för OLEDDisplay. Skickar state/transcript/latency
    till peripheral_bridge.py:s OLED-endpoints istället för lokal I2C."""

    def __init__(self, bridge_host: str, bridge_port: int = 5001):
        self.base_url = f"http://{bridge_host}:{bridge_port}"
        self._session = requests.Session()

    def _post(self, path: str, payload: dict):
        def _send():
            try:
                self._session.post(f"{self.base_url}{path}", json=payload, timeout=HTTP_TIMEOUT)
            except Exception as e:
                log.debug(f"OLED-brygga onåbar ({path}): {e}")
        threading.Thread(target=_send, daemon=True).start()

    def set_state(self, state):
        # state kan vara en HansonState-enum eller sträng — normalisera till str
        state_str = getattr(state, "value", state) if state is not None else "idle"
        self._post("/display/state", {"state": str(state_str)})

    def set_transcript(self, text: str):
        self._post("/display/transcript", {"text": text})

    def set_latency(self, ms: int):
        self._post("/display/latency", {"ms": ms})

    def cleanup(self):
        pass  # inget lokalt att städa upp — bryggan äger hårdvaran


class RemoteInputListener:
    """
    Ansluter som WebSocket-KLIENT till peripheral_bridge.py:s event-server
    (som körs på Hanson-Pi:n) och tar emot knapptryck/PIR-events i realtid.

    Exponerar samma gränssnitt som agent.py:s lokala _read_button()/
    _read_pir() förväntade sig: en flagga som är True en gång per event,
    och sedan självrensas — så huvudloopens polling-logik (last_btn,
    pir_triggered, debounce) kan återanvändas i princip oförändrad.

    Robust mot att bryggan/nätverket är nere: återansluter automatiskt
    med kort fördröjning, och rapporterar helt enkelt inga events (agenten
    fortsätter köra normalt, bara utan fysisk knapp/PIR under tiden).
    """

    def __init__(self, bridge_host: str, bridge_port: int = 8766):
        self.uri = f"ws://{bridge_host}:{bridge_port}"
        self._button_event = threading.Event()
        self._motion_event = threading.Event()
        self._running = False
        self._connected = False

    def start(self):
        self._running = True
        threading.Thread(target=self._run_loop, daemon=True).start()
        log.info(f"Fjärrknapp/PIR-lyssnare startar mot {self.uri}")

    def stop(self):
        self._running = False

    # ── Gränssnitt som matchar agent.py:s förväntningar ────────────────────
    def poll_button(self) -> bool:
        """Returnerar True EXAKT en gång per registrerad knapptryckning."""
        if self._button_event.is_set():
            self._button_event.clear()
            return True
        return False

    def poll_motion(self) -> bool:
        """Returnerar True EXAKT en gång per registrerad PIR-rörelse."""
        if self._motion_event.is_set():
            self._motion_event.clear()
            return True
        return False

    @property
    def connected(self) -> bool:
        return self._connected

    # ── Internt ─────────────────────────────────────────────────────────────
    def _run_loop(self):
        asyncio.run(self._async_loop())

    async def _async_loop(self):
        import websockets
        while self._running:
            try:
                async with websockets.connect(self.uri, open_timeout=3) as ws:
                    self._connected = True
                    log.info("Ansluten till peripheral_bridge (knapp/PIR)")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            if msg.get("type") == "button_press":
                                self._button_event.set()
                            elif msg.get("type") == "motion":
                                self._motion_event.set()
                        except Exception as e:
                            log.debug(f"Ogiltigt event från brygga: {e}")
            except Exception as e:
                if self._connected:
                    log.warning(f"Tappade anslutning till peripheral_bridge: {e}")
                self._connected = False
                await asyncio.sleep(2.0)   # kort paus innan återanslutningsförsök
