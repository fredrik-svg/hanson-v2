# Hanson - ElevenLabs Conversational AI Agent

Voice-activated AI assistant för Raspberry Pi 5 med Debian Trixie. Använder WS2812B RGB LED ring för visuell feedback och stöder både USB-mikrofon och Bluetooth-ljud.

![Raspberry Pi 5](https://img.shields.io/badge/Raspberry%20Pi-5-C51A4A?logo=raspberry-pi)
![Debian Trixie](https://img.shields.io/badge/Debian-Trixie-A81D33?logo=debian)
![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)
![License MIT](https://img.shields.io/badge/License-MIT-green)

## ✨ Features

- 🎤 **ReSpeaker USB Mic Array** - Professionell ljudinspelning med 4-mikrofon array
- 🔊 **Bluetooth Audio** - Trådlös högtalare via PipeWire
- 🌈 **WS2812B RGB LED Ring** - 12 LEDs för visuell statusindikering
- 🔘 **GPIO Button Control** - Fysisk knapp för start/stopp
- 🤖 **ElevenLabs Conversational AI** - Naturliga, realtidskonversationer
- ⚡ **Async Architecture** - Effektiv prestanda
- 🔧 **Trixie Compatible** - Använder lgpio och PipeWire

## 🛠️ Hardware

### Komponenter

| Komponent | Beskrivning | Länk |
|-----------|-------------|------|
| Raspberry Pi 5 | 4GB eller 8GB RAM | [Köp](https://www.raspberrypi.com/products/raspberry-pi-5/) |
| AZ-Delivery WS2812B | RGB LED Ring med 12 LEDs | [Köp](https://www.az-delivery.de/) |
| ReSpeaker USB Mic | 4-mic circular array (USB) | [Köp](https://www.seeedstudio.com/) |
| Bluetooth högtalare | Valfri A2DP-kompatibel | - |
| Tryckknapp | Momentary switch | - |
| Jumper wires | Male-to-female | - |

### Kopplingsschema

```
WS2812B LED Ring:
  DI  → GPIO 18 (Pin 12)
  5V  → 5V (Pin 2)
  GND → GND (Pin 6)
  DO  → Lämnas okopplad

Tryckknapp:
  GPIO 17 (Pin 11) ↔ GND (Pin 9)
  (Intern pull-up används)
```

## 🚀 Snabbstart

### 1. Klona repository

```bash
git clone https://github.com/fredrik-svg/Hanson.git
cd Hanson
```

### 2. Kör installationsskript

```bash
chmod +x setup.sh
./setup.sh
```

Detta installerar:
- Systemberoenden (Python, lgpio, PipeWire, Bluetooth)
- Python virtual environment
- Alla Python-paket
- GPIO-rättigheter

### 3. Konfigurera API-nycklar

```bash
cp .env.example .env
nano .env
```

Lägg till dina ElevenLabs credentials:
```env
ELEVENLABS_API_KEY=sk_your_api_key_here
ELEVENLABS_AGENT_ID=agent_your_agent_id_here
```

Få dina nycklar på:
- API Key: https://elevenlabs.io/app/settings/api-keys
- Agent ID: https://elevenlabs.io/app/conversational-ai

### 4. Para Bluetooth-högtalare

```bash
bluetoothctl
> power on
> agent on
> scan on
> pair XX:XX:XX:XX:XX:XX
> trust XX:XX:XX:XX:XX:XX
> connect XX:XX:XX:XX:XX:XX
> exit
```

### 5. Koppla hårdvara

Se kopplingsschema ovan. Koppla LED-ringen och knappen enligt beskrivningen.

### 6. Kör agenten

```bash
source venv/bin/activate
python3 agent.py
```

## 🎨 LED Status

LED-ringen visar aktuell status:

| Färg | Status | Beskrivning |
|------|--------|-------------|
| 🔴→🟢→🔵 | Startup | Färgcykel vid systemstart |
| 🟢 Puls | Startar | Konversation initieras |
| 🔵 Fast | Lyssnar | Agent lyssnar aktivt (redo att ta emot) |
| 🟢 Pulsering | Användare pratar | Användaren har börjat prata |
| 🟣 Pulsering | Agent pratar | Agenten svarar/pratar |
| 🟠 Puls | Avslutar | Konversation avslutas |
| 🔴 Puls | Fel | Ett fel uppstod |

## 📖 Användning

1. **Starta agenten**: `python3 agent.py`
2. **Vänta på initialisering**: Systemet initierar audio interface (~3s) och LED-ringen blinkar (röd→grön→blå)
3. **Systemet är redo**: Audio interface är nu aktiverat och högtalaren är redo
4. **Tryck knappen**: Startar konversation omedelbart (LED blir blå)
5. **Prata med agenten**: Ställ frågor eller ge kommandon
6. **Tryck knappen igen**: Avslutar konversation (LED släcks)
7. **Stoppa agenten**: Ctrl+C

## 🔧 Konfiguration

### LED-inställningar

I `agent.py`, justera:

```python
LED_COUNT = 12  # Antal LEDs (8, 12, 16 eller 24)
LED_BRIGHTNESS = 0.2  # 0.0-1.0 (0.2 = 20%)
```

### GPIO-pinnar

```python
LED_PIN = board.D18  # GPIO 18 för WS2812B
BUTTON_PIN = 17  # GPIO 17 för knapp
```

### Ljudenheter

Agenten hittar automatiskt ReSpeaker och Bluetooth-högtalare. För att manuellt specificera:

```bash
# Lista tillgängliga enheter
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

## 🤖 Autostart med systemd

För att köra agenten automatiskt vid boot:

```bash
sudo cp systemd/hanson-agent.service /etc/systemd/system/
sudo nano /etc/systemd/system/hanson-agent.service
# Justera sökvägar om nödvändigt

sudo systemctl daemon-reload
sudo systemctl enable hanson-agent
sudo systemctl start hanson-agent
```

Hantera service:

```bash
# Status
sudo systemctl status hanson-agent

# Loggar
sudo journalctl -u hanson-agent -f

# Starta om
sudo systemctl restart hanson-agent

# Stoppa
sudo systemctl stop hanson-agent
```

## 📁 Projektstruktur

```
Hanson/
├── agent.py              # Huvudapplikation
├── setup.sh             # Installationsskript
├── requirements.txt     # Python-beroenden
├── .env.example         # Mall för miljövariabler
├── .gitignore          # Git ignore-fil
├── README.md           # Denna fil
├── LICENSE             # MIT License
└── systemd/
    └── hanson-agent.service  # Systemd service-fil
```

## 🐛 Felsökning

### LED-ringen fungerar inte

```bash
# Kontrollera neopixel-installation
pip show adafruit-circuitpython-neopixel

# Testa LED-ringen manuellt
python3 << EOF
import board
import neopixel
pixels = neopixel.NeoPixel(board.D18, 12, brightness=0.2)
pixels.fill((255, 0, 0))  # Röd
pixels.show()
EOF
```

### Knappen svarar inte

```bash
# Testa knappen
python3 << EOF
import lgpio as GPIO
import time
chip = GPIO.gpiochip_open(4)
GPIO.gpio_claim_input(chip, 17, GPIO.SET_PULL_UP)
for i in range(50):
    print("Knapp:", "Nedtryckt" if GPIO.gpio_read(chip, 17) == 0 else "Uppe")
    time.sleep(0.1)
EOF
```

### Bluetooth-ljud fungerar inte

```bash
# Återanslut
bluetoothctl connect XX:XX:XX:XX:XX:XX

# Sätt som standard
pactl set-default-sink bluez_sink.XX_XX_XX_XX_XX_XX.a2dp_sink

# Starta om PipeWire
systemctl --user restart pipewire-pulse
```

### ReSpeaker hittas inte

```bash
# Lista USB-enheter
lsusb | grep -i seeed

# Lista ljudkort
arecord -l
```

## 🔐 Säkerhet

- **API-nycklar**: Lägg ALDRIG till `.env` i git
- **Systemd**: Service körs med user-rättigheter, inte root
- **GPIO**: Användaren måste vara i `gpio`-gruppen

## 🤝 Bidra

Contributions välkomnas! Öppna en issue eller pull request.

1. Forka projektet
2. Skapa en feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit dina ändringar (`git commit -m 'Add some AmazingFeature'`)
4. Push till branch (`git push origin feature/AmazingFeature`)
5. Öppna en Pull Request

## 📝 License

Detta projekt är licensierat under MIT License - se [LICENSE](LICENSE) för detaljer.

## 🙏 Acknowledgments

- [ElevenLabs](https://elevenlabs.io/) - Conversational AI API
- [Seeed Studio](https://www.seeedstudio.com/) - ReSpeaker hardware
- [AZ-Delivery](https://www.az-delivery.de/) - WS2812B LED ring
- [Raspberry Pi Foundation](https://www.raspberrypi.com/) - Raspberry Pi 5

## 📧 Kontakt

Fredrik - [@fredrik-svg](https://github.com/fredrik-svg)

Project Link: [https://github.com/fredrik-svg/Hanson](https://github.com/fredrik-svg/Hanson)

---

⭐ Om du gillar projektet, ge det en stjärna på GitHub!
