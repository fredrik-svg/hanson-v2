#!/usr/bin/env python3
"""
ElevenLabs Conversational AI Agent för Raspberry Pi 5
Med GPIO-kontroll (WS2812B RGB LED Ring + knapp) och Bluetooth audio
"""

import os
import sys
import signal
import time
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface
from dotenv import load_dotenv

# Ladda miljövariabler
load_dotenv()

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
    
    def read_button(self):
        """Läser knappstatus (True = nedtryckt)"""
        if self.gpio_chip:
            try:
                return GPIO.gpio_read(self.gpio_chip, BUTTON_PIN) == 0
            except Exception as e:
                print(f"Knapp-fel: {e}")
        return False
    
    def start_conversation(self):
        """Startar en konversation"""
        if self.conversation_active:
            return
        
        try:
            print("Startar konversation...")
            
            # Visuell feedback: grön puls
            self.led_pulse_once((0, 255, 0))
            
            # Skapa audio interface
            audio_interface = DefaultAudioInterface()
            
            # Skapa konversation
            self.conversation = Conversation(
                client=self.client,
                agent_id=AGENT_ID,
                requires_auth=True,
                audio_interface=audio_interface,
            )
            
            # Starta session
            self.conversation.start_session()
            self.conversation_active = True
            
            # Lyssnande state: blå/cyan färg
            self.set_led_color((0, 100, 255))
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
        print("  Blå/Cyan = Lyssnar")
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
