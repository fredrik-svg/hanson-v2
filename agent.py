#!/usr/bin/env python3
"""
ElevenLabs Conversational AI Agent för Raspberry Pi 5
Med GPIO-kontroll (WS2812B RGB LED Ring + knapp) och Bluetooth audio
"""

import os
import sys
import signal
import time
import threading
from contextlib import contextmanager
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface
from dotenv import load_dotenv

# Ladda miljövariabler
load_dotenv()


@contextmanager
def suppress_alsa_errors():
    """
    Kontexthanterare för att tysta ALSA-fel/varningar.
    ALSA skriver felmeddelanden direkt till stderr, vilket kan kludda konsolutmatningen.
    Detta händer när PyAudio/sounddevice initierar ljudgränssnitt på Raspberry Pi.
    """
    # Öppna /dev/null för att omdirigera felutskrifter
    devnull = os.open(os.devnull, os.O_WRONLY)
    # Spara nuvarande stderr
    old_stderr = os.dup(2)
    # Omdirigera stderr till /dev/null
    os.dup2(devnull, 2)
    os.close(devnull)
    
    try:
        yield
    finally:
        # Återställ stderr
        os.dup2(old_stderr, 2)
        os.close(old_stderr)

# WS2812B LED Ring
try:
    import board
    import neopixel
    NEOPIXEL_AVAILABLE = True
except ImportError:
    NEOPIXEL_AVAILABLE = False
    print("Varning: neopixel/board saknas - LED ring inaktiverad")
    print("Installera med: pip install adafruit-circuitpython-neopixel")

# GPIO för knapp
try:
    import lgpio as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("Varning: lgpio saknas - knapp inaktiverad")

# Konfiguration
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID")

# WS2812B LED Ring konfiguration
LED_PIN = board.D18  # GPIO 18 (används av neopixel library)
LED_COUNT = 16  # Antal LEDs i ringen (AZ-Delivery har vanligtvis 8, 12, 16 eller 24)
LED_BRIGHTNESS = 0.3  # 0.0-1.0 (30% brightness)

# Knapp
BUTTON_PIN = 17  # GPIO 17

# LED state timeout constants (seconds)
USER_SPEAKING_TIMEOUT = 3.0  # Timeout after user finishes speaking
AGENT_SPEAKING_TIMEOUT = 2.0  # Timeout after agent finishes speaking
SPEAKER_STARTUP_DELAY = 1.5  # Delay to allow speaker to activate before audio plays


class RaspberryPiAgent:
    """Agent som hanterar konversation, GPIO och ljudgränssnitt"""
    
    def __init__(self):
        """Initierar agenten"""
        # Validera API-nycklar
        if not ELEVENLABS_API_KEY:
            print("Fel: ELEVENLABS_API_KEY saknas i .env")
            sys.exit(1)
        if not AGENT_ID:
            print("Fel: ELEVENLABS_AGENT_ID saknas i .env")
            sys.exit(1)
        
        # Initialisera klient
        self.client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        self.conversation = None
        self.gpio_chip = None
        self.pixels = None
        self.conversation_active = False
        
        # LED effect control
        self.led_effect_thread = None
        self.led_effect_stop_event = threading.Event()
        self.current_led_state = "idle"  # idle, listening, user_speaking, agent_speaking
        self.state_lock = threading.Lock()  # Lock for thread-safe state changes
        self._listening_timer = None
        
        # Setup LED ring och knapp
        self.setup_led_ring()
        self.setup_button()
    
    def setup_led_ring(self):
        """Initierar WS2812B LED-ring"""
        if not NEOPIXEL_AVAILABLE:
            return
        
        try:
            self.pixels = neopixel.NeoPixel(
                LED_PIN,
                LED_COUNT,
                brightness=LED_BRIGHTNESS,
                auto_write=False,
                pixel_order=neopixel.GRB
            )
            print(f"✓ LED Ring: {LED_COUNT} LEDs på GPIO 18")
            self.led_startup_animation()
        except Exception as e:
            print(f"LED Ring-fel: {e}")
            self.pixels = None
    
    def setup_button(self):
        """Initierar GPIO för knapp"""
        if not GPIO_AVAILABLE:
            return
        
        try:
            self.gpio_chip = GPIO.gpiochip_open(4)
            GPIO.gpio_claim_input(self.gpio_chip, BUTTON_PIN, GPIO.SET_PULL_UP)
            print(f"✓ Knapp: GPIO {BUTTON_PIN}")
        except Exception as e:
            print(f"Knapp-fel: {e}")
            self.gpio_chip = None
    
    def set_led_color(self, color, brightness=None):
        """
        Sätter alla LEDs till en färg
        color: tuple (R, G, B) 0-255
        """
        if not self.pixels:
            return
        
        try:
            if brightness is not None:
                old_brightness = self.pixels.brightness
                self.pixels.brightness = brightness
            
            self.pixels.fill(color)
            self.pixels.show()
            
            if brightness is not None:
                self.pixels.brightness = old_brightness
        except Exception as e:
            print(f"LED-fel: {e}")
    
    def set_led_off(self):
        """Släcker alla LEDs"""
        self.set_led_color((0, 0, 0))
    
    def led_startup_animation(self):
        """Startup-animation: färgcykel"""
        if not self.pixels:
            return
        
        colors = [
            (255, 0, 0),    # Röd
            (0, 255, 0),    # Grön
            (0, 0, 255),    # Blå
        ]
        
        for color in colors:
            self.set_led_color(color)
            time.sleep(0.3)
        
        self.set_led_off()
    
    def led_breathing_effect(self, color=(0, 100, 255), duration=2.0, steps=20):
        """
        Andningseffekt - pulserande ljus
        Användbart för "listening" state
        """
        if not self.pixels:
            return
        
        for _ in range(int(duration)):
            # Fade in
            for i in range(steps):
                brightness = i / steps * LED_BRIGHTNESS
                self.set_led_color(color, brightness)
                time.sleep(0.05)
            
            # Fade out
            for i in range(steps, 0, -1):
                brightness = i / steps * LED_BRIGHTNESS
                self.set_led_color(color, brightness)
                time.sleep(0.05)
    
    def led_spinner_effect(self, color=(0, 255, 100), duration=2.0):
        """
        Spinner-effekt - roterande ljus
        Användbart för "thinking" state
        """
        if not self.pixels:
            return
        
        steps = 20
        delay = duration / (LED_COUNT * steps)
        
        for _ in range(steps):
            for i in range(LED_COUNT):
                self.pixels.fill((0, 0, 0))
                self.pixels[i] = color
                self.pixels.show()
                time.sleep(delay)
    
    def led_pulse_once(self, color=(0, 255, 0)):
        """Snabb puls - för bekräftelse"""
        if not self.pixels:
            return
        
        for brightness in [0.1, 0.3, 0.5, 0.8, 1.0, 0.8, 0.5, 0.3, 0.1]:
            self.set_led_color(color, brightness * LED_BRIGHTNESS)
            time.sleep(0.05)
        self.set_led_off()
    
    def stop_led_effect(self):
        """Stoppar pågående LED-effekt"""
        if self.led_effect_thread and self.led_effect_thread.is_alive():
            self.led_effect_stop_event.set()
            # Use shorter timeout to prevent blocking, thread is daemon so it will cleanup anyway
            self.led_effect_thread.join(timeout=0.5)
        self.led_effect_stop_event.clear()
    
    def start_led_effect(self, effect_name, **kwargs):
        """Startar en kontinuerlig LED-effekt i en separat tråd"""
        # Stoppa eventuell pågående effekt
        self.stop_led_effect()
        
        # Starta ny effekt
        if effect_name == "listening":
            self.led_effect_thread = threading.Thread(
                target=self._led_listening_effect_loop,
                daemon=True
            )
        elif effect_name == "user_speaking":
            self.led_effect_thread = threading.Thread(
                target=self._led_user_speaking_effect_loop,
                daemon=True
            )
        elif effect_name == "agent_speaking":
            self.led_effect_thread = threading.Thread(
                target=self._led_agent_speaking_effect_loop,
                daemon=True
            )
        else:
            return
        
        self.led_effect_thread.start()
    
    def _led_listening_effect_loop(self):
        """Kontinuerlig effekt för listening state - solid blå/cyan"""
        while not self.led_effect_stop_event.is_set():
            if self.pixels:
                self.set_led_color((0, 100, 255))  # Cyan/blå
            time.sleep(0.1)
    
    def _led_pulse_effect_loop(self, color):
        """Generisk pulserande effekt med given färg"""
        while not self.led_effect_stop_event.is_set():
            if self.pixels:
                # Fade in
                for i in range(20):
                    if self.led_effect_stop_event.is_set():
                        return
                    brightness = i / 20 * LED_BRIGHTNESS
                    self.set_led_color(color, brightness)
                    time.sleep(0.025)
                
                # Fade out
                for i in range(20, 0, -1):
                    if self.led_effect_stop_event.is_set():
                        return
                    brightness = i / 20 * LED_BRIGHTNESS
                    self.set_led_color(color, brightness)
                    time.sleep(0.025)
    
    def _led_user_speaking_effect_loop(self):
        """Kontinuerlig effekt för user speaking - pulserande grön"""
        self._led_pulse_effect_loop((0, 255, 0))  # Grön
    
    def _led_agent_speaking_effect_loop(self):
        """Kontinuerlig effekt för agent speaking - pulserande lila/magenta"""
        self._led_pulse_effect_loop((200, 0, 255))  # Lila/magenta
    
    def read_button(self):
        """Läser knappstatus (True = nedtryckt)"""
        if self.gpio_chip:
            try:
                return GPIO.gpio_read(self.gpio_chip, BUTTON_PIN) == 0
            except Exception as e:
                print(f"Knapp-fel: {e}")
        return False
    
    # Callback methods för Conversation events
    def on_user_transcript(self, transcript: str):
        """Callback när användaren pratar (transkription mottagen)"""
        print(f"Användare: {transcript}")
        
        with self.state_lock:
            # Avbryt eventuell pågående timer
            if self._listening_timer is not None:
                self._listening_timer.cancel()
            
            # Uppdatera LED state om nödvändigt
            if self.current_led_state != "user_speaking":
                self.current_led_state = "user_speaking"
                self.start_led_effect("user_speaking")
            
            # Schemalägg återgång till listening efter timeout
            self._listening_timer = threading.Timer(USER_SPEAKING_TIMEOUT, self._return_to_listening)
            self._listening_timer.daemon = True
            self._listening_timer.start()
    
    def on_agent_response(self, response: str):
        """Callback när agenten svarar"""
        print(f"Agent: {response}")
        
        with self.state_lock:
            # Avbryt eventuell pågående timer
            if self._listening_timer is not None:
                self._listening_timer.cancel()
            
            # Uppdatera LED state om nödvändigt
            if self.current_led_state != "agent_speaking":
                self.current_led_state = "agent_speaking"
                self.start_led_effect("agent_speaking")
            
            # Schemalägg återgång till listening efter timeout
            self._listening_timer = threading.Timer(AGENT_SPEAKING_TIMEOUT, self._return_to_listening)
            self._listening_timer.daemon = True
            self._listening_timer.start()
    
    def _return_to_listening(self):
        """Återgår till listening state om konversationen är aktiv"""
        with self.state_lock:
            if self.conversation_active and self.current_led_state != "listening":
                self.current_led_state = "listening"
                self.start_led_effect("listening")
    
    def start_conversation(self):
        """Startar en konversation"""
        if self.conversation_active:
            return
        
        try:
            print("Startar konversation...")
            
            # Visuell feedback: grön puls
            self.led_pulse_once((0, 255, 0))
            
            # Skapa audio interface med ALSA-fel undertryckta
            with suppress_alsa_errors():
                audio_interface = DefaultAudioInterface()
            
            # Vänta för att låta högtalaren aktiveras innan ljud spelas
            print(f"Väntar {SPEAKER_STARTUP_DELAY}s för högtalare att aktiveras...")
            time.sleep(SPEAKER_STARTUP_DELAY)
            
            # Skapa konversation med callbacks
            self.conversation = Conversation(
                client=self.client,
                agent_id=AGENT_ID,
                requires_auth=True,
                audio_interface=audio_interface,
                callback_user_transcript=self.on_user_transcript,
                callback_agent_response=self.on_agent_response,
            )
            
            # Starta session
            self.conversation.start_session()
            self.conversation_active = True
            
            # Lyssnande state: starta kontinuerlig listening effekt
            self.current_led_state = "listening"
            self.start_led_effect("listening")
            print("✓ Konversation aktiv - prata nu!")
            
        except Exception as e:
            print(f"Fel vid start: {e}")
            # Röd blink vid fel
            self.led_pulse_once((255, 0, 0))
            self.set_led_off()
            self.conversation_active = False
    
    def end_conversation(self):
        """Avslutar konversationen"""
        if not self.conversation_active:
            return
        
        print("Avslutar konversation...")
        
        # Avbryt eventuell listening timer (thread-safe)
        with self.state_lock:
            if self._listening_timer is not None:
                self._listening_timer.cancel()
                self._listening_timer = None
        
        # Stoppa LED-effekt
        self.stop_led_effect()
        
        # Orange puls vid avslut
        self.led_pulse_once((255, 100, 0))
        
        try:
            if self.conversation:
                self.conversation.end_session()
        except Exception as e:
            print(f"Varning vid avslut: {e}")
        finally:
            self.set_led_off()
            self.conversation_active = False
            self.conversation = None
            self.current_led_state = "idle"
            print("✓ Konversation avslutad")
    
    def run(self):
        """Huvudloop som övervakar knappen"""
        print("\n" + "=" * 50)
        print("ElevenLabs Agent - Raspberry Pi 5")
        print("med WS2812B RGB LED Ring")
        print("=" * 50)
        print(f"Agent ID: {AGENT_ID}")
        print(f"LED Count: {LED_COUNT}")
        print(f"Button Pin: GPIO {BUTTON_PIN}")
        print("\nLED Status:")
        print("  Grön puls = Konversation startar")
        print("  Blå/Cyan solid = Lyssnar (redo att ta emot)")
        print("  Grön pulsering = Användaren pratar")
        print("  Lila/Magenta pulsering = Agenten pratar")
        print("  Orange puls = Avslutar")
        print("  Röd puls = Fel")
        print("\nTryck knappen för att starta/stoppa konversation")
        print("Ctrl+C för att avsluta\n")
        
        last_button_state = False
        
        try:
            while True:
                button_pressed = self.read_button()
                
                # Detektera nedtryckning (flank)
                if button_pressed and not last_button_state:
                    if not self.conversation_active:
                        self.start_conversation()
                    else:
                        self.end_conversation()
                
                last_button_state = button_pressed
                time.sleep(0.05)  # 50ms polling
                
        except KeyboardInterrupt:
            print("\n\nAvslutar...")
            self.cleanup()
    
    def cleanup(self):
        """Städar upp resurser"""
        # Avsluta konversation om aktiv
        if self.conversation_active:
            try:
                if self.conversation:
                    self.conversation.end_session()
            except:
                pass
        
        # Stoppa LED-effekter
        self.stop_led_effect()
        
        # Släck LEDs
        self.set_led_off()
        
        # Stäng GPIO
        if self.gpio_chip:
            try:
                GPIO.gpiochip_close(self.gpio_chip)
            except:
                pass
        
        print("✓ Avstängd")


def main():
    """Main entry point"""
    agent = RaspberryPiAgent()
    agent.run()


if __name__ == "__main__":
    main()
