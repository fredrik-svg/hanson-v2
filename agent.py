#!/usr/bin/env python3
"""
ElevenLabs Conversational AI Agent för Raspberry Pi 5
Med GPIO-kontroll (LED + knapp) och Bluetooth audio
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

# GPIO-hantering
try:
    import lgpio as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("Varning: lgpio saknas - LED och knapp inaktiverade")

# Konfiguration
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID")
LED_PIN = 18
BUTTON_PIN = 17


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
        self.conversation_active = False
        
        # Setup GPIO
        self.setup_gpio()
    
    def setup_gpio(self):
        """Initierar GPIO för LED och knapp"""
        if not GPIO_AVAILABLE:
            return
        
        try:
            self.gpio_chip = GPIO.gpiochip_open(4)
            GPIO.gpio_claim_output(self.gpio_chip, LED_PIN, 0)
            GPIO.gpio_claim_input(self.gpio_chip, BUTTON_PIN, GPIO.SET_PULL_UP)
            print(f"✓ GPIO: LED={LED_PIN}, Knapp={BUTTON_PIN}")
            self.blink_led(3)
        except Exception as e:
            print(f"GPIO-fel: {e}")
            self.gpio_chip = None
    
    def set_led(self, state):
        """Tänder/släcker LED"""
        if self.gpio_chip:
            try:
                GPIO.gpio_write(self.gpio_chip, LED_PIN, 1 if state else 0)
            except Exception as e:
                print(f"LED-fel: {e}")
    
    def blink_led(self, times=3):
        """Blinkar LED"""
        for _ in range(times):
            self.set_led(True)
            time.sleep(0.2)
            self.set_led(False)
            time.sleep(0.2)
    
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
            self.set_led(True)
            
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
            print("✓ Konversation aktiv - prata nu!")
            
        except Exception as e:
            print(f"Fel vid start: {e}")
            self.set_led(False)
            self.conversation_active = False
    
    def end_conversation(self):
        """Avslutar konversationen"""
        if not self.conversation_active:
            return
        
        print("Avslutar konversation...")
        try:
            if self.conversation:
                self.conversation.end_session()
        except Exception as e:
            print(f"Varning vid avslut: {e}")
        finally:
            self.set_led(False)
            self.conversation_active = False
            self.conversation = None
            print("✓ Konversation avslutad")
    
    def run(self):
        """Huvudloop som övervakar knappen"""
        print("\n" + "=" * 50)
        print("ElevenLabs Agent - Raspberry Pi 5")
        print("=" * 50)
        print(f"Agent ID: {AGENT_ID}")
        print(f"LED Pin: {LED_PIN}")
        print(f"Button Pin: {BUTTON_PIN}")
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
        
        # Släck LED
        self.set_led(False)
        
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
