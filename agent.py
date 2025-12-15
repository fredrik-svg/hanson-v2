#!/usr/bin/env python3
import os, sys, signal
from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface
from dotenv import load_dotenv

load_dotenv()

try:
    import lgpio as GPIO
    GPIO_AVAILABLE = True
except:
    GPIO_AVAILABLE = False
    print("Varning: lgpio saknas - LED och knapp inaktiverade")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID")
LED_PIN, BUTTON_PIN = 18, 17

class RaspberryPiAgent:
    def __init__(self):
        if not ELEVENLABS_API_KEY:
            print("Fel: ELEVENLABS_API_KEY saknas i .env")
            sys.exit(1)
        if not AGENT_ID:
            print("Fel: ELEVENLABS_AGENT_ID saknas i .env")
            sys.exit(1)
            
        self.client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        self.conversation = None
        self.gpio_chip = None
        self.setup_gpio()
    
    def setup_gpio(self):
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
        if self.gpio_chip:
            try:
                GPIO.gpio_write(self.gpio_chip, LED_PIN, 1 if state else 0)
            except:
                pass
    
    def blink_led(self, times=3):
        import time
        for _ in range(times):
            self.set_led(True)
            time.sleep(0.2)
            self.set_led(False)
            time.sleep(0.2)
    
    def read_button(self):
        if self.gpio_chip:
            try:
                return GPIO.gpio_read(self.gpio_chip, BUTTON_PIN) == 0
            except:
                pass
        return False
    
    def run(self):
        print("\n=== ElevenLabs Agent startad ===")
        print(f"Agent ID: {AGENT_ID}")
        
        # Skapa audio interface
        audio_interface = DefaultAudioInterface()
        
        # Skapa konversation
        self.conversation = Conversation(
            client=self.client,
            agent_id=AGENT_ID,
            requires_auth=True,
            audio_interface=audio_interface,
        )
        
        print("Tryck knappen för att starta konversation...")
        print("Ctrl+C för att avsluta\n")
        
        # Övervaka knappen
        import time
        last_button_state = False
        conversation_active = False
        
        try:
            while True:
                button_pressed = self.read_button()
                
                # Knapp nedtryckt (flank)
                if button_pressed and not last_button_state:
                    if not conversation_active:
                        print("Startar konversation...")
                        self.set_led(True)
                        self.conversation.start_session()
                        conversation_active = True
                        print("Konversation aktiv - prata nu!")
                    else:
                        print("Avslutar konversation...")
                        self.conversation.end_session()
                        self.set_led(False)
                        conversation_active = False
                        print("Konversation avslutad")
                
                last_button_state = button_pressed
                time.sleep(0.05)
                
        except KeyboardInterrupt:
            print("\nAvslutar...")
            if conversation_active:
                self.conversation.end_session()
            self.cleanup()
    
    def cleanup(self):
        self.set_led(False)
        if self.gpio_chip:
            try:
                GPIO.gpiochip_close(self.gpio_chip)
            except:
                pass
        print("Avstängd")

if __name__ == "__main__":
    agent = RaspberryPiAgent()
    agent.run()
