#!/usr/bin/env python3
"""
Hanson v3 - agent.py (förenklad version efter hårdvaruuppgradering)

Hårdvara:
  Mikrofon  → ReSpeaker 4 Mic Array, AEC/AGC aktiverat i firmware (XVF-3000 DSP)
  Högtalare → Waveshare USB-ljudkort, native 16kHz (ingen resampling behövs)
  LED-ring  → WS2812B via Pi5Neo, SPI0 (GPIO 10)
  Skärm     → DollaTek SH1106 OLED 128x64, I2C
  Knapp     → GPIO 17
  PIR       → GPIO 27

SESSIONSMODELL (viktigt):
  Knappen är den ENDA startaren och avslutaren av en konversation.
  - Tryck när ingen session pågår → startar konversation
  - Tryck när en session pågår    → avslutar konversation
  - Konversationen kan även avslutas av dialogen själv (ElevenLabs
    turn-detection/timeout → callback_end_session)

  PIR är ENDAST kosmetisk: en kort LED-blink/skärmuppdatering vid rörelse
  när Hanson står i viloläge, för att signalera "jag märker dig". Den
  startar ALDRIG en konversation och påverkar ALDRIG en pågående session.
  Detta eliminerar tidigare race conditions kring PIR-triggade starter
  mitt i en aktiv konversation.

VIKTIGT — fyll i dessa innan körning:
  INPUT_DEVICE  = device-index för ReSpeaker (kör list_audio_devices() för att se)
  OUTPUT_DEVICE = device-index för Waveshare USB-ljudkort

Eftersom ReSpeakerns AEC nu tar bort eko i hårdvaran, och Waveshare-kortet
kör samma 16kHz som ElevenLabs native, har vi kunnat ta bort:
  - All resample_poly/scipy-logik
  - mic_muted / generation-counter eko-skydd
  - seconds_remaining()-beräkningar
  - Hela den manuella buffer-hanteringen för väntan

Output-callbacken är dock kvar pull-baserad (inte blocking write()) för
att garantera korrekt ljudtiming även för långa svar.
"""

import os
import sys
import time
import threading
import logging

import numpy as np
import sounddevice as sd

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import (
    Conversation,
    ConversationInitiationData,
    ClientTools,
    AgentChatResponsePartType,
    AudioInterface,
)
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hanson")

# ── GPIO ───────────────────────────────────────────────────────────────────────
try:
    import lgpio as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    log.warning("lgpio saknas")

# ── LED via Pi5Neo ─────────────────────────────────────────────────────────────
try:
    from pi5neo import Pi5Neo
    LED_AVAILABLE = True
except ImportError:
    LED_AVAILABLE = False
    log.warning("pi5neo saknas")

# ── OLED via luma.oled (valfritt, kan köras utan om skärm inte kopplad än) ─────
DISPLAY_AVAILABLE = False
try:
    from display import OLEDDisplay, HansonState
    DISPLAY_AVAILABLE = True
except ImportError as e:
    log.warning(f"display.py/luma.oled inte tillgängligt ({e}) — kör utan skärm")
except Exception as e:
    log.error(f"Oväntat fel vid import av display.py ({e}) — kör utan skärm", exc_info=True)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID           = os.getenv("ELEVENLABS_AGENT_ID")

LED_COUNT  = 16
BUTTON_PIN = 17
PIR_PIN    = 27

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  FYLL I DESSA EFTER ATT DU KÖRT list_audio_devices() PÅ PI:N             ║
# ╚══════════════════════════════════════════════════════════════════════════╝
INPUT_DEVICE  = 1   # ← ReSpeaker 4 Mic Array device-index
OUTPUT_DEVICE = 2   # ← Waveshare USB-ljudkort device-index

# Båda enheterna kör nu samma sample rate — ingen konvertering behövs
SAMPLE_RATE  = 16000
CHANNELS_IN  = 6        # ReSpeaker har 6 kanaler, vi använder kanal 0
CHANNELS_OUT = 1        # Mono ut (Waveshare-kortet, justera till 2 om det kräver stereo)
BLOCKSIZE    = 1024

PIR_DEBOUNCE_SECONDS     = 0.5     # Ignorera PIR-flanker snabbare än detta (skydd mot darrande sensor)
MAX_CONVERSATION_SECONDS = 300.0
MAX_CONVERSATIONS_PER_HOUR = 60    # Skydd mot skenande API-kostnad vid skadegörelse/fel


def list_audio_devices():
    """
    Hjälpfunktion: kör denna för att hitta rätt device-index innan du fyller
    i INPUT_DEVICE / OUTPUT_DEVICE ovan.

        python3 -c "from agent import list_audio_devices; list_audio_devices()"
    """
    print("\nTillgängliga ljudenheter:")
    for i, d in enumerate(sd.query_devices()):
        print(f"  [{i}] {d['name']}  in:{d['max_input_channels']} out:{d['max_output_channels']} "
              f"rate:{int(d['default_samplerate'])}")
    print()


# ══════════════════════════════════════════════════════════════════════════════
class HansonAudioInterface(AudioInterface):
    """
    Förenklad AudioInterface — ingen resampling, ingen mute-logik.
    ReSpeakerns hårdvaru-AEC hanterar eko, matchande 16kHz sample rate
    eliminerar behovet av konvertering.
    """

    def __init__(self):
        self.in_stream     = None
        self.out_stream    = None
        self._out_buffer   = np.zeros((0, CHANNELS_OUT), dtype=np.int16)
        self._buffer_lock  = threading.Lock()

    def start(self, input_callback):
        def _in_callback(indata, frames, time_info, status):
            if status:
                log.debug(f"Input status: {status}")
            mono = indata[:, 0].copy()   # Kanal 0 från ReSpeaker
            input_callback(mono.tobytes())

        self.in_stream = sd.InputStream(
            device=INPUT_DEVICE,
            channels=CHANNELS_IN,
            samplerate=SAMPLE_RATE,
            dtype="int16",
            blocksize=BLOCKSIZE,
            callback=_in_callback,
        )
        self.in_stream.start()

        # Output: callback-baserad (pull), INTE blocking write(). Detta är
        # kritiskt: en blockerande write() i en separat tråd kan tappa synk
        # mot ljudkortets klocka för långa svar. Med en callback styr
        # hårdvaran exakt när nästa bit ljud hämtas, vilket är samma mönster
        # ElevenLabs egen DefaultAudioInterface använder via PyAudio.
        def _out_callback(outdata, frames, time_info, status):
            if status:
                log.debug(f"Output status: {status}")
            with self._buffer_lock:
                available = len(self._out_buffer)
                if available >= frames:
                    outdata[:] = self._out_buffer[:frames]
                    self._out_buffer = self._out_buffer[frames:]
                elif available > 0:
                    outdata[:available] = self._out_buffer
                    outdata[available:] = 0
                    self._out_buffer = np.zeros((0, CHANNELS_OUT), dtype=np.int16)
                else:
                    outdata[:] = 0

        self.out_stream = sd.OutputStream(
            device=OUTPUT_DEVICE,
            channels=CHANNELS_OUT,
            samplerate=SAMPLE_RATE,
            dtype="int16",
            blocksize=BLOCKSIZE,
            callback=_out_callback,
        )
        self.out_stream.start()

        log.info(f"Audio: input=device{INPUT_DEVICE} output=device{OUTPUT_DEVICE} "
                 f"(matchande {SAMPLE_RATE}Hz, ingen resampling)")

    def stop(self):
        if self.in_stream:
            try:
                self.in_stream.stop()
                self.in_stream.close()
            except Exception:
                pass
            self.in_stream = None
        if self.out_stream:
            try:
                self.out_stream.stop()
                self.out_stream.close()
            except Exception:
                pass
            self.out_stream = None
        with self._buffer_lock:
            self._out_buffer = np.zeros((0, CHANNELS_OUT), dtype=np.int16)

    def output(self, audio: bytes):
        """Tar emot 16kHz mono PCM16 från ElevenLabs, lägger i buffer för callback att plocka."""
        try:
            samples = np.frombuffer(audio, dtype=np.int16)
            if CHANNELS_OUT == 2:
                samples = np.column_stack((samples, samples))
            else:
                samples = samples.reshape(-1, 1)
            with self._buffer_lock:
                self._out_buffer = np.concatenate([self._out_buffer, samples])
        except Exception as e:
            log.debug(f"Output-fel: {e}")

    def interrupt(self):
        """Töm output-bufferten omedelbart (t.ex. om agenten avbryts)."""
        with self._buffer_lock:
            self._out_buffer = np.zeros((0, CHANNELS_OUT), dtype=np.int16)

    def output_buffer_seconds(self) -> float:
        """Hjälpmetod för diagnostik: hur många sekunder ljud ligger köat."""
        with self._buffer_lock:
            return len(self._out_buffer) / SAMPLE_RATE

    def cleanup(self):
        self.stop()


# ══════════════════════════════════════════════════════════════════════════════
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
        for r, g, b in [(255,0,0), (0,255,0), (0,0,255)]:
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
            self._fill(int(r*level), int(g*level), int(b*level))
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
                if self._stop.is_set(): return
                lv = i / steps
                self._fill(int(r*lv), int(g*lv), int(b*lv))
                time.sleep(0.025)
            for i in range(steps, 0, -1):
                if self._stop.is_set(): return
                lv = i / steps
                self._fill(int(r*lv), int(g*lv), int(b*lv))
                time.sleep(0.025)

    def _spinner_loop(self, color):
        if not self.neo:
            return
        r, g, b = color
        while not self._stop.is_set():
            for i in range(LED_COUNT):
                if self._stop.is_set(): return
                try:
                    self.neo.fill_strip(0, 0, 0)
                    self.neo.set_led_color(i, r, g, b)
                    self.neo.set_led_color((i-1) % LED_COUNT, r//3, g//3, b//3)
                    self.neo.set_led_color((i-2) % LED_COUNT, r//8, g//8, b//8)
                    self.neo.update_strip()
                except Exception:
                    pass
                time.sleep(0.06)

    def tool_set_color(self, params: dict) -> str:
        r = int(params.get("r", 0))
        g = int(params.get("g", 0))
        b = int(params.get("b", 0))
        self.stop_effect()
        self._fill(r, g, b)
        return f"LED satt till ({r},{g},{b})"

    def tool_run_animation(self, params: dict) -> str:
        color_map = {
            "red":    (255,0,0), "green": (0,255,0), "blue":   (0,0,255),
            "purple": (200,0,255), "orange": (255,165,0),
            "white":  (255,255,255), "cyan": (0,200,255),
        }
        name  = params.get("animation", "pulse")
        color = color_map.get(params.get("color", "green"), (0,255,0))
        if name == "pulse":     self._run(self._pulse_loop, color=color)
        elif name == "spinner": self._run(self._spinner_loop, color=color)
        elif name == "flash":   threading.Thread(target=self.pulse_once, args=(color,), daemon=True).start()
        elif name == "off":     self.stop_effect(); self._fill(0,0,0)
        return f"Animation '{name}' startad"

    def cleanup(self):
        self.stop_effect()
        self._fill(0, 0, 0)


# ══════════════════════════════════════════════════════════════════════════════
class RaspberryPiAgent:

    def __init__(self):
        if not ELEVENLABS_API_KEY:
            log.error("ELEVENLABS_API_KEY saknas i .env")
            sys.exit(1)
        if not AGENT_ID:
            log.error("ELEVENLABS_AGENT_ID saknas i .env")
            sys.exit(1)
        if INPUT_DEVICE is None or OUTPUT_DEVICE is None:
            log.error(
                "INPUT_DEVICE/OUTPUT_DEVICE är inte ifyllda i agent.py! "
                "Kör list_audio_devices() för att hitta rätt index, "
                "fyll i värdena överst i filen, och försök igen."
            )
            sys.exit(1)

        self.client              = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        self.led                 = LEDController()
        self.display = None
        if DISPLAY_AVAILABLE:
            try:
                self.display = OLEDDisplay()
            except Exception as e:
                log.error(f"Display kunde inte initieras ({e}) — fortsätter utan skärm", exc_info=True)
                self.display = None
        self.gpio_chip           = None
        self.conversation        = None
        self.conversation_active = False
        self._current_audio      = None
        self._session_started_at = 0.0
        self._last_session_end   = 0.0
        self._session_lock       = threading.Lock()
        self._last_pir_edge      = 0.0
        self._conversation_start_times = []   # För MAX_CONVERSATIONS_PER_HOUR

        self._setup_gpio()
        self._verify_audio()

    def _setup_gpio(self):
        if not GPIO_AVAILABLE:
            return
        try:
            self.gpio_chip = GPIO.gpiochip_open(4)
            GPIO.gpio_claim_input(self.gpio_chip, BUTTON_PIN, GPIO.SET_PULL_UP)
            GPIO.gpio_claim_input(self.gpio_chip, PIR_PIN,    GPIO.SET_PULL_DOWN)
            log.info(f"GPIO: Knapp=GPIO{BUTTON_PIN}, PIR=GPIO{PIR_PIN}")
        except Exception as e:
            log.error(f"GPIO-fel: {e}")
            self.gpio_chip = None

    def _verify_audio(self):
        try:
            devices = sd.query_devices()
            for idx, label in [(INPUT_DEVICE, "Input"), (OUTPUT_DEVICE, "Output")]:
                if idx < len(devices):
                    d = devices[idx]
                    log.info(f"{label} (device {idx}): {d['name']} "
                             f"in:{d['max_input_channels']} out:{d['max_output_channels']}")
                else:
                    log.warning(f"{label} device {idx} finns inte!")
        except Exception as e:
            log.error(f"Audio-verifieringsfel: {e}")

    def _build_client_tools(self) -> ClientTools:
        ct = ClientTools()
        ct.register("set_led_color",     self.led.tool_set_color,    is_async=False)
        ct.register("run_led_animation", self.led.tool_run_animation, is_async=False)
        return ct

    def _set_display_state(self, state):
        if self.display:
            try:
                self.display.set_state(state)
            except Exception:
                pass

    def _on_user_transcript(self, transcript: str):
        try:
            log.info(f"Användare: {transcript}")
            self.led.start_thinking()
            if self.display:
                self.display.set_transcript(transcript)
            self._set_display_state(HansonState.THINKING if DISPLAY_AVAILABLE else None)
        except Exception as e:
            log.error(f"Fel i _on_user_transcript: {e}", exc_info=True)

    def _on_agent_response(self, response: str):
        try:
            log.info(f"Agent: {response}")
        except Exception as e:
            log.error(f"Fel i _on_agent_response: {e}", exc_info=True)

    def _on_agent_response_correction(self, original: str, corrected: str):
        try:
            log.info(f"Agent (korrigering): '{original[:40]}'")
        except Exception as e:
            log.error(f"Fel i _on_agent_response_correction: {e}", exc_info=True)

    def _on_agent_chat_response_part(self, text: str, part_type: AgentChatResponsePartType):
        try:
            if part_type == AgentChatResponsePartType.START:
                self.led.start_agent_speaking()
                self._set_display_state(HansonState.SPEAKING if DISPLAY_AVAILABLE else None)
            elif part_type == AgentChatResponsePartType.STOP:
                if self.conversation_active:
                    self.led.start_listening()
                    self._set_display_state(HansonState.LISTENING if DISPLAY_AVAILABLE else None)
        except Exception as e:
            log.error(f"Fel i _on_agent_chat_response_part: {e}", exc_info=True)

    def _on_latency(self, latency_ms: int):
        try:
            log.info(f"Latens: {latency_ms}ms")
            if self.display:
                self.display.set_latency(latency_ms)
        except Exception as e:
            log.error(f"Fel i _on_latency: {e}", exc_info=True)

    def _on_end_session(self):
        try:
            log.info("Session avslutad (av ElevenLabs eller lokalt)")
            self._cleanup_after_session()
        except Exception as e:
            log.error(f"Fel i _on_end_session: {e}", exc_info=True)
            with self._session_lock:
                self.conversation_active = False

    def _cleanup_after_session(self):
        with self._session_lock:
            if not self.conversation_active:
                return
            self.conversation_active = False
            self._last_session_end   = time.time()
        self.led.stop_effect()
        self.led.pulse_once(LEDController.ENDING)
        self._set_display_state(HansonState.IDLE if DISPLAY_AVAILABLE else None)
        log.info("Redo för nästa konversation")

    def _rate_limit_ok(self) -> bool:
        """
        Skydd mot skenande API-kostnad: max MAX_CONVERSATIONS_PER_HOUR
        konversationer per rullande timme. Relevant om PIR triggar
        oavsiktligt mycket (skadegörelse, lekande barn, sensorfel) eller
        om någon spammar knappen.
        """
        now = time.time()
        self._conversation_start_times = [
            t for t in self._conversation_start_times if now - t < 3600
        ]
        if len(self._conversation_start_times) >= MAX_CONVERSATIONS_PER_HOUR:
            log.warning(
                f"Rate-limit nådd: {MAX_CONVERSATIONS_PER_HOUR} konversationer/timme. "
                f"Avvisar nytt försök tillfälligt."
            )
            return False
        return True

    def start_conversation(self, trigger: str = "knapp"):
        if not self._rate_limit_ok():
            self.led.pulse_once(LEDController.ERROR)
            return

        with self._session_lock:
            if self.conversation_active:
                return
            self.conversation_active = True

        self._conversation_start_times.append(time.time())

        audio = HansonAudioInterface()
        self._current_audio = audio

        try:
            log.info(f"Startar konversation (trigger: {trigger})…")
            self.led.pulse_once(LEDController.SUCCESS)

            self.conversation = Conversation(
                client=self.client,
                agent_id=AGENT_ID,
                requires_auth=True,
                audio_interface=audio,
                config=ConversationInitiationData(),
                client_tools=self._build_client_tools(),
                callback_user_transcript=self._on_user_transcript,
                callback_agent_response=self._on_agent_response,
                callback_agent_response_correction=self._on_agent_response_correction,
                callback_agent_chat_response_part=self._on_agent_chat_response_part,
                callback_latency_measurement=self._on_latency,
                callback_end_session=self._on_end_session,
            )

            self.conversation.start_session()
            self._session_started_at = time.time()

            time.sleep(0.3)
            try:
                self.conversation.send_contextual_update(
                    f"Konversationen startades via {trigger}. Hälsa besökaren välkommen på svenska."
                )
            except Exception as e:
                log.debug(f"Contextual update misslyckades (ofarligt): {e}")

            self.led.start_listening()
            self._set_display_state(HansonState.LISTENING if DISPLAY_AVAILABLE else None)
            log.info("Konversation aktiv!")

            self._start_watchdog()

        except Exception as e:
            log.error(f"Startfel: {e}", exc_info=True)
            self.led.pulse_once(LEDController.ERROR)
            with self._session_lock:
                self.conversation_active = False
            try:
                audio.cleanup()
            except Exception:
                pass
            self._current_audio = None
            self.conversation = None

    def _start_watchdog(self):
        def _watchdog():
            time.sleep(MAX_CONVERSATION_SECONDS)
            with self._session_lock:
                still_same = self.conversation_active
            if still_same:
                log.warning(f"Watchdog: konversation aktiv >{MAX_CONVERSATION_SECONDS}s — tvingar cleanup")
                self._force_cleanup()
        threading.Thread(target=_watchdog, daemon=True).start()

    def _force_cleanup(self):
        try:
            if self.conversation:
                try:
                    self.conversation.end_session()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self._current_audio:
                self._current_audio.cleanup()
        except Exception:
            pass
        self._cleanup_after_session()
        self.conversation   = None
        self._current_audio = None

    def end_conversation(self):
        with self._session_lock:
            if not self.conversation_active:
                return

        log.info("Avslutar konversation…")
        try:
            if self.conversation:
                self.conversation.end_session()

                result_holder = {}
                def _wait():
                    try:
                        result_holder["id"] = self.conversation.wait_for_session_end()
                    except Exception as e:
                        result_holder["error"] = e

                wait_thread = threading.Thread(target=_wait, daemon=True)
                wait_thread.start()
                wait_thread.join(timeout=5.0)

                if wait_thread.is_alive():
                    log.warning("wait_for_session_end() svarade inte inom 5s — fortsätter ändå")
                elif "id" in result_holder and result_holder["id"]:
                    log.info(f"Conversation ID: {result_holder['id']}")
        except Exception as e:
            log.warning(f"Avslutsvarning: {e}")
        finally:
            self._cleanup_after_session()
            self.conversation = None
            self._current_audio = None

    def _cosmetic_wake(self):
        """
        Körs när PIR detekterar rörelse OCH ingen konversation pågår.
        Helt kosmetisk: en kort LED-blink/skärmuppdatering som signalerar
        "jag märker att du är här" utan att starta en session. Knappen är
        fortfarande det enda sättet att faktiskt börja prata med Hanson.
        Körs i en daemon-tråd så huvudloopen aldrig blockeras av detta.
        """
        def _wake_animation():
            try:
                # Bara om fortfarande ingen session är aktiv när tråden hinner köra
                if self.conversation_active:
                    return
                self.led.pulse_once(LEDController.MOTION)
                self._set_display_state(HansonState.MOTION if DISPLAY_AVAILABLE else None)
                time.sleep(1.5)
                if not self.conversation_active:
                    self._set_display_state(HansonState.IDLE if DISPLAY_AVAILABLE else None)
            except Exception as e:
                log.debug(f"Fel i cosmetic_wake (ofarligt): {e}")

        threading.Thread(target=_wake_animation, daemon=True).start()

    def _read_button(self) -> bool:
        if not self.gpio_chip:
            return False
        try:
            return GPIO.gpio_read(self.gpio_chip, BUTTON_PIN) == 0
        except Exception:
            return False

    def _read_pir(self) -> bool:
        if not self.gpio_chip:
            return False
        try:
            return GPIO.gpio_read(self.gpio_chip, PIR_PIN) == 1
        except Exception:
            return False

    def _check_audio_devices_healthy(self) -> bool:
        try:
            devices = sd.query_devices()
            return INPUT_DEVICE < len(devices) and OUTPUT_DEVICE < len(devices)
        except Exception as e:
            log.error(f"Audio-hälsokontroll misslyckades: {e}")
            return False

    def run(self):
        print()
        print("=" * 52)
        print("  Hanson v3 – Förenklad (AEC + matchande sample rate)")
        print("=" * 52)
        print(f"  Input  : device {INPUT_DEVICE} ({SAMPLE_RATE}Hz {CHANNELS_IN}ch)")
        print(f"  Output : device {OUTPUT_DEVICE} ({SAMPLE_RATE}Hz {CHANNELS_OUT}ch)")
        print(f"  Knapp  : GPIO {BUTTON_PIN}  (start/avslut av session)")
        print(f"  PIR    : GPIO {PIR_PIN}    (endast kosmetisk väckning, ingen sessionspåverkan)")
        print(f"  Skärm  : {'Aktiverad' if DISPLAY_AVAILABLE else 'Ej ansluten/installerad'}")
        print()
        print("  Tryck knappen för att starta eller avsluta en konversation.")
        print("  Rörelse väcker bara LED/skärm kosmetiskt — påverkar aldrig en aktiv session.")
        print("  Ctrl+C för att avsluta")
        print()

        last_btn      = False
        pir_triggered = False
        last_health_check = time.time()
        HEALTH_CHECK_INTERVAL = 60.0

        try:
            while True:
                now = time.time()
                if (now - last_health_check) > HEALTH_CHECK_INTERVAL:
                    last_health_check = now
                    if not self.conversation_active:
                        if not self._check_audio_devices_healthy():
                            log.error("Audio-enheter saknas! Kontrollera USB-anslutningar.")

                # ── KNAPP: enda sessionsstartaren och -avslutaren ──────────
                btn = self._read_button()
                if btn and not last_btn:
                    if not self.conversation_active:
                        log.info("Knapp: startar ny konversation")
                        self.start_conversation(trigger="knapp")
                    else:
                        log.info("Knapp: avslutar aktiv konversation")
                        self.end_conversation()
                last_btn = btn

                # ── PIR: ENDAST kosmetisk väckning, ALDRIG sessionspåverkan ──
                # Rör aldrig conversation_active, start_conversation eller
                # end_conversation. Om en konversation redan pågår ignoreras
                # PIR helt och hållet — det är meningen.
                pir = self._read_pir()
                if pir and not pir_triggered:
                    now_pir = time.time()
                    debounced = (now_pir - self._last_pir_edge) < PIR_DEBOUNCE_SECONDS
                    if not debounced:
                        self._last_pir_edge = now_pir
                        if not self.conversation_active:
                            log.debug("Rörelse detekterad (kosmetisk väckning, ingen session startas)")
                            self._cosmetic_wake()
                pir_triggered = pir

                time.sleep(0.05)

        except KeyboardInterrupt:
            print("\nAvslutar…")
        finally:
            self.cleanup()
            os._exit(0)

    def cleanup(self):
        if self.conversation_active:
            try:
                self.conversation.end_session()
            except Exception:
                pass
        self.led.cleanup()
        if self.display:
            try:
                self.display.cleanup()
            except Exception:
                pass
        if self.gpio_chip:
            try:
                GPIO.gpiochip_close(self.gpio_chip)
            except Exception:
                pass
        log.info("Hanson avstängd")


def main():
    restart_count = 0
    max_restarts_per_hour = 10
    restart_times = []

    while True:
        try:
            agent = RaspberryPiAgent()
            agent.run()
            break
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"OVÄNTAD KRASCH i huvudprocessen: {e}", exc_info=True)

            now = time.time()
            restart_times.append(now)
            restart_times = [t for t in restart_times if now - t < 3600]

            if len(restart_times) > max_restarts_per_hour:
                log.error(
                    f"Mer än {max_restarts_per_hour} omstarter senaste timmen — "
                    f"avbryter för att undvika crash-loop."
                )
                break

            log.warning("Startar om Hanson om 3 sekunder…")
            time.sleep(3)


if __name__ == "__main__":
    main()
