#!/bin/bash
set -e
echo "=== Setup ElevenLabs Pi5 Agent ==="
echo "Uppdaterar system..."
sudo apt update
echo "Installerar paket..."
sudo apt install -y python3-pip python3-venv python3-lgpio pipewire pipewire-pulse pipewire-alsa wireplumber bluez bluez-tools libportaudio2 portaudio19-dev git curl
echo "Konfigurerar PipeWire..."
systemctl --user enable pipewire pipewire-pulse wireplumber 2>/dev/null || true
systemctl --user start pipewire pipewire-pulse wireplumber 2>/dev/null || true
echo "Aktiverar Bluetooth..."
sudo systemctl enable bluetooth
sudo systemctl start bluetooth
echo "GPIO-rättigheter..."
sudo usermod -a -G gpio $USER || true
echo "Python venv..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Redigera .env med dina nycklar: nano .env"
fi
echo ""
echo "=== Setup klar! ==="
echo "1. Redigera .env: nano .env"
echo "2. Para Bluetooth-högtalare med bluetoothctl"
echo "3. Koppla hårdvara (se docs/HARDWARE.md)"
echo "4. Kör: source venv/bin/activate && python3 agent.py"
