# ElevenLabs Agent for Raspberry Pi 5

Voice-activated AI assistant with ElevenLabs Conversational AI on Raspberry Pi 5 + Debian Trixie.

## Features
- 🎤 ReSpeaker USB Mic Array
- 🔊 Bluetooth audio output  
- 🔴 GPIO LED status indicator
- 🔘 Physical button control
- ⚡ Async architecture
- 🔧 Trixie compatible (lgpio + PipeWire)

## Hardware
- Raspberry Pi 5
- ReSpeaker USB Mic Array
- Bluetooth speaker
- LED + 220Ω resistor
- Momentary button
- Jumper wires

## Quick Start
```bash
# Extract/clone project
cd elevenlabs-pi5-agent

# Run setup
chmod +x setup.sh
./setup.sh

# Configure
cp .env.example .env
nano .env  # Add your API keys

# Connect hardware
# LED: GPIO 18 → LED+ → 220Ω → GND
# Button: GPIO 17 ↔ GND

# Pair Bluetooth speaker
bluetoothctl
> power on
> scan on
> pair XX:XX:XX:XX:XX:XX
> trust XX:XX:XX:XX:XX:XX  
> connect XX:XX:XX:XX:XX:XX

# Run
source venv/bin/activate
python3 agent.py
```

## Usage
1. LED blinks 3x = ready
2. Press button = start conversation
3. LED solid = conversation active
4. Press again = end conversation

## Autostart
```bash
sudo cp systemd/elevenlabs-agent.service /etc/systemd/system/
sudo systemctl enable elevenlabs-agent
sudo systemctl start elevenlabs-agent
```

## Documentation
- `docs/INSTALLATION.md` - Detailed setup
- `docs/HARDWARE.md` - Wiring diagrams
- `docs/TROUBLESHOOTING.md` - Common issues

## License
MIT License - see LICENSE file
