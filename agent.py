#!/usr/bin/env python3
import os,sys,asyncio,signal
from typing import Optional
from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation
import sounddevice as sd
import numpy as np
from dotenv import load_dotenv
load_dotenv()
try:
    import lgpio as GPIO
    GPIO_AVAILABLE=True
except: GPIO_AVAILABLE=False;print("lgpio saknas")
ELEVENLABS_API_KEY=os.getenv("ELEVENLABS_API_KEY")
AGENT_ID=os.getenv("ELEVENLABS_AGENT_ID")
LED_PIN,BUTTON_PIN=18,17
SAMPLE_RATE,CHANNELS,CHUNK_SIZE=16000,1,1024
class RaspberryPiAgent:
    def __init__(s):
        s.client=None;s.conversation=None;s.gpio_chip=None;s.running=False;s.in_conversation=False
        s.input_device=s._find_device("ReSpeaker")
        s.output_device=s._find_device("bluealsa",output=True)
    def _find_device(s,name,output=False):
        for i,d in enumerate(sd.query_devices()):
            if name.lower() in d['name'].lower():
                if output and d['max_output_channels']>0:return i
                elif not output and d['max_input_channels']>0:return i
        return None
    def setup_gpio(s):
        if not GPIO_AVAILABLE:return
        try:
            s.gpio_chip=GPIO.gpiochip_open(4)
            GPIO.gpio_claim_output(s.gpio_chip,LED_PIN,0)
            GPIO.gpio_claim_input(s.gpio_chip,BUTTON_PIN,GPIO.SET_PULL_UP)
            print(f"GPIO OK: LED={LED_PIN}, Knapp={BUTTON_PIN}")
        except Exception as e:print(f"GPIO-fel:{e}");s.gpio_chip=None
    def set_led(s,state):
        if s.gpio_chip:
            try:GPIO.gpio_write(s.gpio_chip,LED_PIN,1 if state else 0)
            except:pass
    def read_button(s):
        if s.gpio_chip:
            try:return GPIO.gpio_read(s.gpio_chip,BUTTON_PIN)==0
            except:pass
        return False
    async def monitor_button(s):
        last=False
        while s.running:
            cur=s.read_button()
            if cur and not last:
                print("Knapp tryckt")
                if not s.in_conversation:asyncio.create_task(s.start_conversation())
            last=cur
            await asyncio.sleep(0.05)
    def blink_led(s,times=3,delay=0.2):
        import time
        for _ in range(times):s.set_led(True);time.sleep(delay);s.set_led(False);time.sleep(delay)
    async def start_conversation(s):
        if s.in_conversation:return
        s.in_conversation=True;s.set_led(True)
        try:
            print("Startar...")
            s.conversation=await s.client.conversational_ai.conversation.start(agent_id=AGENT_ID)
            inp={'samplerate':SAMPLE_RATE,'channels':CHANNELS,'dtype':np.int16,'blocksize':CHUNK_SIZE}
            if s.input_device:inp['device']=s.input_device
            outp={'samplerate':SAMPLE_RATE,'channels':CHANNELS,'dtype':np.int16}
            if s.output_device:outp['device']=s.output_device
            with sd.InputStream(**inp) as stream:
                print("Lyssnar...")
                async def audio_cb():
                    while s.in_conversation:
                        d,_=stream.read(CHUNK_SIZE)
                        await s.conversation.send_audio(d.tobytes())
                        await asyncio.sleep(0.01)
                async def resp_handler():
                    async for r in s.conversation.receive_audio():
                        if r.audio_event:
                            a=np.frombuffer(r.audio_event.audio,dtype=np.int16)
                            sd.play(a,**outp);sd.wait()
                await asyncio.gather(audio_cb(),resp_handler())
        except Exception as e:print(f"Fel:{e}")
        finally:s.in_conversation=False;s.set_led(False);print("Avslutad")
    async def run(s):
        if not ELEVENLABS_API_KEY or not AGENT_ID:print("Fel: API-nycklar saknas");return
        s.client=ElevenLabs(api_key=ELEVENLABS_API_KEY)
        s.setup_gpio();s.blink_led(3)
        print("\n=== Agent startad ===")
        print(f"Mic:{s.input_device or 'Standard'}")
        print(f"Speaker:{s.output_device or 'Standard'}")
        print(f"Tryck GPIO {BUTTON_PIN}\n")
        s.running=True
        try:await s.monitor_button()
        except asyncio.CancelledError:pass
        finally:s.cleanup()
    def cleanup(s):
        print("\nStänger...");s.running=False;s.set_led(False)
        if s.gpio_chip:
            try:GPIO.gpiochip_close(s.gpio_chip)
            except:pass
async def main():
    a=RaspberryPiAgent()
    l=asyncio.get_event_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):
        l.add_signal_handler(sig,lambda:asyncio.create_task(shutdown(a)))
    await a.run()
async def shutdown(a):
    a.running=False
    ts=[t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in ts:t.cancel()
    await asyncio.gather(*ts,return_exceptions=True)
if __name__=="__main__":
    try:asyncio.run(main())
    except KeyboardInterrupt:print("\nAvslut");sys.exit(0)
