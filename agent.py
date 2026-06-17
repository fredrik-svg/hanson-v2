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
import queue

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

PIR_COOLDOWN_AFTER_END = 10.0


# ══════════════════════════════════════════════════════════════════════════════
class HansonAudioInterface(AudioInterface):
    """
    Modern AudioInterface via sounddevice/numpy/scipy.

    Input:  ReSpeaker (6ch int16 @16kHz) → kanal 0 extraheras → bytes till ElevenLabs
    Output: ElevenLabs (mono int16 @16kHz) → resample_poly → 48kHz → duplicera till stereo
    """

    def __init__(self):
        self.in_stream   = None
        self.out_stream  = None
        self._out_queue  = queue.Queue()
        self._out_thread = None
        self._running    = False

    def start(self, input_callback):
        self._running = True

        # ── Input callback ──────────────────────────────────────────────
        def _in_callback(indata, frames, time_info, status):
            if status:
                log.debug(f"Input status: {status}")
            # indata: shape (frames, CHANNELS_IN), dtype int16
            mono = indata[:, 0].copy()          # Kanal 0 från ReSpeaker
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

        # ── Output stream ───────────────────────────────────────────────
        self.out_stream = sd.OutputStream(
            device=OUTPUT_DEVICE,
            channels=CHANNELS_OUT,
            samplerate=HW_RATE_OUT,
            dtype="int16",
            blocksize=BLOCKSIZE,
        )
        self.out_stream.start()

        self._out_thread = threading.Thread(target=self._output_loop, daemon=True)
        self._out_thread.start()

        log.info(f"Audio: input=device{INPUT_DEVICE} ({HW_RATE_IN}Hz {CHANNELS_IN}ch) "
                 f"output=device{OUTPUT_DEVICE} ({HW_RATE_OUT}Hz {CHANNELS_OUT}ch)")

    def _output_loop(self):
        while self._running:
            try:
                stereo_chunk = self._out_queue.get(timeout=0.1)
                if self.out_stream:
                    self.out_stream.write(stereo_chunk)
            except queue.Empty:
                continue
            except Exception as e:
                log.debug(f"Output-fel: {e}")

    def stop(self):
        self._running = False
        if self._out_thread:
            self._out_thread.join(timeout=2.0)
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

    def output(self, audio: bytes):
        """
        Tar emot 16kHz mono PCM16 från ElevenLabs.
        Resamplar till 48kHz och duplicerar till stereo.
        """
        mono16k = np.frombuffer(audio, dtype=np.int16)

        # Resample 16kHz → 48kHz (3x upsampling, exakt heltalsförhållande)
        # resample_poly är polyphase-baserad: hög kvalitet, ingen state behövs
        mono48k = resample_poly(mono16k, up=3, down=1).astype(np.int16)

        # Mono → Stereo (duplicera till båda kanaler)
        stereo = np.column_stack((mono48k, mono48k))

        self._out_queue.put(stereo)

    def interrupt(self):
        """Töm output-kön omedelbart vid avbrott (t.ex. användaren börjar prata)."""
        while not self._out_queue.empty():
            try:
                self._out_queue.get_nowait()
            except queue.Empty:
                break

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
        log.info(f"Användare: {transcript}")
        self.led.start_thinking()

    def _on_agent_response(self, response: str):
        log.info(f"Agent: {response}")

    def _on_agent_response_correction(self, original: str, corrected: str):
        log.info(f"Agent (korrigering): '{original[:40]}'")

    def _on_agent_chat_response_part(self, text: str, part_type: AgentChatResponsePartType):
        if part_type == AgentChatResponsePartType.START:
            self.led.start_agent_speaking()
        elif part_type == AgentChatResponsePartType.STOP:
            if self.conversation_active:
                self.led.start_listening()

    def _on_latency(self, latency_ms: int):
        log.info(f"Latens: {latency_ms}ms")

    def _on_end_session(self):
        log.info("Session avslutad")
        self._cleanup_after_session()

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

            # Vänta kort så websocket hinner etableras innan vi skickar något
            time.sleep(0.3)
            try:
                self.conversation.send_contextual_update(
                    f"Konversationen startades via {trigger}. Hälsa besökaren välkommen på svenska."
                )
            except Exception as e:
                log.debug(f"Contextual update misslyckades (ofarligt): {e}")

            self.led.start_listening()
            log.info("Konversation aktiv!")

        except Exception as e:
            log.error(f"Startfel: {e}")
            self.led.pulse_once(LEDController.ERROR)
            with self._session_lock:
                self.conversation_active = False
            audio.cleanup()

    def end_conversation(self):
        with self._session_lock:
            if not self.conversation_active:
                return

        log.info("Avslutar konversation…")
        try:
            if self.conversation:
                self.conversation.end_session()
                conversation_id = self.conversation.wait_for_session_end()
                if conversation_id:
                    log.info(f"Conversation ID: {conversation_id}")
        except Exception as e:
            log.warning(f"Avslutsvarning: {e}")
        finally:
            self._cleanup_after_session()
            self.conversation = None

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

        try:
            while True:
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
    agent = RaspberryPiAgent()
    agent.run()


if __name__ == "__main__":
    main()
