#!/usr/bin/env python3
"""
Hanson v3 - agent.py (steg 1: knapp + LED)
Audio via sounddevice + numpy/scipy (modern, framtidssäker stack)

Input  → ReSpeaker 4 Mic Array (index 1, 6ch, 16kHz) → kanal 0 → ElevenLabs
Output → ElevenLabs (16kHz mono) → resample → USB-högtalare (index 2, 48kHz stereo)
"""

import os
import sys
import time
import threading
import logging

import numpy as np
import sounddevice as sd
from scipy.signal import resample_poly

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

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID           = os.getenv("ELEVENLABS_AGENT_ID")

LED_COUNT  = 16
BUTTON_PIN = 17
PIR_PIN    = 27

# Audio-enheter (sounddevice device-index, ej samma som PyAudio nödvändigtvis)
INPUT_DEVICE  = 1   # ReSpeaker 4 Mic Array
OUTPUT_DEVICE = 2   # USB-högtalare

ELEVENLABS_RATE = 16000   # ElevenLabs skickar/förväntar alltid 16kHz mono PCM16
HW_RATE_IN      = 16000   # ReSpeaker native sample rate
HW_RATE_OUT     = 48000   # USB-högtalare native sample rate
CHANNELS_IN     = 6       # ReSpeaker har 6 kanaler, vi använder kanal 0
CHANNELS_OUT    = 2       # USB-högtalare stereo
BLOCKSIZE       = 1024

PIR_COOLDOWN_AFTER_END   = 10.0
MAX_CONVERSATION_SECONDS = 300.0   # Watchdog: tvinga avslut efter 5 min oavsett


# ══════════════════════════════════════════════════════════════════════════════
class HansonAudioInterface(AudioInterface):
    """
    Modern AudioInterface via sounddevice/numpy/scipy.

    Input:  ReSpeaker (6ch int16 @16kHz) → kanal 0 extraheras → bytes till ElevenLabs
    Output: ElevenLabs (mono int16 @16kHz) → resample_poly → 48kHz → duplicera till stereo
    """

    def __init__(self):
        self.in_stream    = None
        self.out_stream   = None
        self._out_buffer  = np.zeros((0, CHANNELS_OUT), dtype=np.int16)
        self._buffer_lock = threading.Lock()
        self._running     = False
        self.mic_muted    = False   # Sätts True medan agenten pratar

    def start(self, input_callback):
        self._running = True

        # ── Input callback ──────────────────────────────────────────────
        def _in_callback(indata, frames, time_info, status):
            if status:
                log.debug(f"Input status: {status}")
            if self.mic_muted:
                return   # Skicka inget till ElevenLabs medan agenten pratar
            mono = indata[:, 0].copy()
            input_callback(mono.tobytes())

        self.in_stream = sd.InputStream(
            device=INPUT_DEVICE,
            channels=CHANNELS_IN,
            samplerate=HW_RATE_IN,
            dtype="int16",
            blocksize=BLOCKSIZE,
            callback=_in_callback,
        )
        self.in_stream.start()

        # ── Output callback: pull-baserad, alltid synkad med ljudkortets klocka ──
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
                    outdata[:] = 0   # Tystnad om bufferten är tom

        self.out_stream = sd.OutputStream(
            device=OUTPUT_DEVICE,
            channels=CHANNELS_OUT,
            samplerate=HW_RATE_OUT,
            dtype="int16",
            blocksize=BLOCKSIZE,
            callback=_out_callback,
        )
        self.out_stream.start()

        log.info(f"Audio: input=device{INPUT_DEVICE} ({HW_RATE_IN}Hz {CHANNELS_IN}ch) "
                 f"output=device{OUTPUT_DEVICE} ({HW_RATE_OUT}Hz {CHANNELS_OUT}ch)")

    def stop(self):
        self._running = False
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
        """
        Tar emot 16kHz mono PCM16 från ElevenLabs.
        Resamplar till 48kHz, duplicerar till stereo, lägger i buffer.
        Output-callbacken plockar därifrån i sin egen takt (ljudkortets klocka).
        """
        mono16k = np.frombuffer(audio, dtype=np.int16)
        mono48k = resample_poly(mono16k, up=3, down=1).astype(np.int16)
        stereo  = np.column_stack((mono48k, mono48k))

        with self._buffer_lock:
            self._out_buffer = np.concatenate([self._out_buffer, stereo])

    def interrupt(self):
        """Töm output-bufferten omedelbart vid avbrott."""
        with self._buffer_lock:
            self._out_buffer = np.zeros((0, CHANNELS_OUT), dtype=np.int16)

    def samples_remaining(self) -> int:
        """Antal samples (frames) som ännu inte spelats ut av högtalaren."""
        with self._buffer_lock:
            return len(self._out_buffer)

    def seconds_remaining(self) -> float:
        """
        Exakt återstående speltid i sekunder: vår egen kö-buffer plus
        PortAudios rapporterade output-latens (hårdvarubufferten).
        Detta är deterministiskt — ingen gissning, bara aritmetik.
        """
        queued_seconds = self.samples_remaining() / HW_RATE_OUT
        hw_latency = self.out_stream.latency if self.out_stream else 0.0
        return queued_seconds + hw_latency

    def output_buffer_empty(self) -> bool:
        with self._buffer_lock:
            return len(self._out_buffer) == 0

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

        self.client              = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        self.led                 = LEDController()
        self.gpio_chip           = None
        self.conversation        = None
        self.conversation_active = False
        self._current_audio      = None
        self._mute_generation    = 0
        self._session_started_at = 0.0
        self._last_session_end   = 0.0
        self._session_lock       = threading.Lock()

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

    def _on_user_transcript(self, transcript: str):
        try:
            log.info(f"Användare: {transcript}")
            self.led.start_thinking()
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
                self._mute_generation += 1
                self.led.start_agent_speaking()
                if self._current_audio:
                    self._current_audio.mic_muted = True
                    log.debug(f"Mic MUTED (gen={self._mute_generation})")
            elif part_type == AgentChatResponsePartType.STOP:
                if self._current_audio:
                    my_gen = self._mute_generation
                    threading.Thread(
                        target=self._unmute_when_silent, args=(my_gen,), daemon=True
                    ).start()
                if self.conversation_active:
                    self.led.start_listening()
        except Exception as e:
            log.error(f"Fel i _on_agent_chat_response_part: {e}", exc_info=True)
            # Failsafe: om något går snett, se till att mikrofonen inte fastnar muted
            try:
                if self._current_audio:
                    self._current_audio.mic_muted = False
            except Exception:
                pass

    def _unmute_when_silent(self, my_gen: int):
        """
        Pollar 'seconds_remaining' kontinuerligt (eftersom mer ljud kan strömma
        in från ElevenLabs medan vi väntar) tills den faktiskt når noll, plus
        en liten säkerhetsmarginal för rumseko. Slår sedan på mikrofonen igen
        — om inget nytt yttrande hunnit börja under tiden (generation-check).

        Hela kroppen är skyddad av try/except: detta körs i en daemon-tråd
        och en okontrollerad exception här ska aldrig kunna låsa mikrofonen
        i muted-läge permanent för resten av entréns drifttid.
        """
        try:
            audio = self._current_audio
            if not audio:
                return

            max_wait = 15.0
            waited = 0.0
            poll_interval = 0.05

            while waited < max_wait:
                remaining = audio.seconds_remaining()
                if remaining <= 0.02:
                    break
                time.sleep(poll_interval)
                waited += poll_interval

            time.sleep(0.3)

            if my_gen != self._mute_generation:
                log.debug(f"Unmute avbruten (gen={my_gen} != aktuell={self._mute_generation})")
                return

            if audio:
                audio.mic_muted = False
                log.debug(f"Mic UNMUTED (gen={my_gen}, väntade {waited:.2f}s)")

        except Exception as e:
            log.error(f"Fel i _unmute_when_silent: {e}", exc_info=True)
            # Failsafe: även vid oväntat fel, försök slå på mikrofonen igen
            # hellre än att lämna besökaren i ett dövt läge.
            try:
                if self._current_audio:
                    self._current_audio.mic_muted = False
            except Exception:
                pass

    def _on_latency(self, latency_ms: int):
        log.info(f"Latens: {latency_ms}ms")

    def _on_end_session(self):
        try:
            log.info("Session avslutad (av ElevenLabs eller lokalt)")
            self._cleanup_after_session()
        except Exception as e:
            log.error(f"Fel i _on_end_session: {e}", exc_info=True)
            # Failsafe: oavsett vad, se till att conversation_active inte fastnar True
            with self._session_lock:
                self.conversation_active = False

    def _cleanup_after_session(self):
        with self._session_lock:
            self.conversation_active = False
            self._last_session_end   = time.time()
        self.led.stop_effect()
        self.led.pulse_once(LEDController.ENDING)
        log.info("Redo för nästa konversation")

    def start_conversation(self, trigger: str = "knapp"):
        with self._session_lock:
            if self.conversation_active:
                return
            self.conversation_active = True

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

            # start_session() startar en bakgrundstråd. Den blockerar inte,
            # men om uppkopplingen hänger vill vi inte vänta i evighet senare.
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
            log.info("Konversation aktiv!")

            # Starta en watchdog som tvingar igenom cleanup om sessionen
            # av någon anledning aldrig avslutas korrekt (hängande websocket etc.)
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
        """
        Säkerhetsnät: om en konversation av någon anledning lever längre än
        MAX_CONVERSATION_SECONDS (hängande websocket, krasch i callback utan
        att _on_end_session triggas, etc.) tvingar vi igenom en cleanup så
        att nästa besökare i entrén inte blockeras på obestämd tid.
        """
        def _watchdog():
            time.sleep(MAX_CONVERSATION_SECONDS)
            with self._session_lock:
                still_same_session = self.conversation_active
            if still_same_session:
                log.warning(
                    f"Watchdog: konversation aktiv längre än {MAX_CONVERSATION_SECONDS}s "
                    f"— tvingar cleanup"
                )
                self._force_cleanup()

        threading.Thread(target=_watchdog, daemon=True).start()

    def _force_cleanup(self):
        """Tvingar igenom full cleanup oavsett state — används av watchdog och felhantering."""
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

                # wait_for_session_end() kan i värsta fall hänga om websocket
                # är i ett konstigt state — körs därför i en separat tråd med
                # hård timeout så huvudloopen ALDRIG fastnar här.
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

    def _pir_in_cooldown(self) -> bool:
        return (time.time() - self._last_session_end) < PIR_COOLDOWN_AFTER_END

    def _check_audio_devices_healthy(self) -> bool:
        """
        Snabb kontroll att INPUT_DEVICE/OUTPUT_DEVICE fortfarande existerar.
        USB-ljudenheter på Pi kan ibland tappa anslutning efter lång drift —
        detta upptäcker det innan nästa besökare drabbas av en tyst krasch.
        """
        try:
            devices = sd.query_devices()
            return INPUT_DEVICE < len(devices) and OUTPUT_DEVICE < len(devices)
        except Exception as e:
            log.error(f"Audio-hälsokontroll misslyckades: {e}")
            return False

    def run(self):
        print()
        print("=" * 52)
        print("  Hanson v3 – Steg 1: Knapp + LED")
        print("=" * 52)
        print(f"  Input  : device {INPUT_DEVICE} (ReSpeaker, {HW_RATE_IN}Hz {CHANNELS_IN}ch)")
        print(f"  Output : device {OUTPUT_DEVICE} (USB-högtalare, {HW_RATE_OUT}Hz {CHANNELS_OUT}ch)")
        print(f"  Knapp  : GPIO {BUTTON_PIN}")
        print(f"  PIR    : GPIO {PIR_PIN}")
        print()
        print("  Tryck knappen eller rör dig framför PIR")
        print("  Ctrl+C för att avsluta")
        print()

        last_btn      = False
        pir_triggered = False
        last_health_check = time.time()
        HEALTH_CHECK_INTERVAL = 60.0   # Kontrollera audio-enheter var 60s

        try:
            while True:
                # ── Periodisk hälsokontroll (bara när ingen konversation pågår) ──
                now = time.time()
                if (now - last_health_check) > HEALTH_CHECK_INTERVAL:
                    last_health_check = now
                    if not self.conversation_active:
                        if not self._check_audio_devices_healthy():
                            log.error(
                                "Audio-enheter saknas! Kontrollera USB-anslutningar. "
                                "Hanson kommer fortsätta försöka, men ljud kan saknas."
                            )

                btn = self._read_button()
                if btn and not last_btn:
                    if not self.conversation_active:
                        log.info("Knapp: startar ny konversation")
                        self.start_conversation(trigger="knapp")
                    else:
                        log.info("Knapp: avslutar aktiv konversation")
                        self.end_conversation()
                last_btn = btn

                pir = self._read_pir()
                if pir and not pir_triggered:
                    if not self.conversation_active and not self._pir_in_cooldown():
                        log.info("Rörelse detekterad")
                        self.led.start_motion()
                        time.sleep(0.8)
                        self.start_conversation(trigger="rörelse")
                pir_triggered = pir

                time.sleep(0.05)

        except KeyboardInterrupt:
            print("\nAvslutar…")
        finally:
            self.cleanup()
            os._exit(0)   # Tvinga total avstängning, inkl. ev. hängande bakgrundstrådar

    def cleanup(self):
        if self.conversation_active:
            try:
                self.conversation.end_session()
            except Exception:
                pass
        self.led.cleanup()
        if self.gpio_chip:
            try:
                GPIO.gpiochip_close(self.gpio_chip)
            except Exception:
                pass
        log.info("Hanson avstängd")


def main():
    """
    Yttre skyddsloop: om RaspberryPiAgent kraschar helt oväntat (t.ex. ett
    odokumenterat SDK-fel som inte fångas av interna try/except), startar
    vi om hela agenten automatiskt istället för att lämna entrén utan
    fungerande Hanson. Begränsad omstartsfrekvens för att undvika crash-loop
    vid ett permanent fel (t.ex. fel API-nyckel).
    """
    restart_count = 0
    max_restarts_per_hour = 10
    restart_times = []

    while True:
        try:
            agent = RaspberryPiAgent()
            agent.run()
            break   # run() avslutas bara vid Ctrl+C (os._exit), så detta nås sällan
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
                    f"avbryter för att undvika crash-loop. Kontrollera felet manuellt."
                )
                break

            log.warning("Startar om Hanson om 3 sekunder…")
            time.sleep(3)


if __name__ == "__main__":
    main()
