#!/usr/bin/env python3
import sounddevice as sd
import numpy as np
print("=== Audio Devices ===")
for i, d in enumerate(sd.query_devices()):
    print(f"{i}: {d['name']}")
    print(f"   In:{d['max_input_channels']} Out:{d['max_output_channels']}")
print("\nTest: Record 3s + playback")
input("Press Enter...")
r = sd.rec(48000, samplerate=16000, channels=1, dtype=np.int16)
sd.wait()
print("Playing...")
sd.play(r, samplerate=16000)
sd.wait()
print("Done!")
