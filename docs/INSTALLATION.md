# Installation Guide

## Method 1: Automated
```bash
chmod +x setup.sh
./setup.sh
```

## Method 2: Manual

### Install dependencies
```bash
sudo apt update
sudo apt install -y python3-pip python3-venv python3-lgpio \
    pipewire pipewire-pulse bluez libportaudio2 portaudio19-dev
```

### Enable services
```bash
systemctl --user enable pipewire pipewire-pulse
sudo systemctl enable bluetooth
sudo usermod -a -G gpio $USER
```
Log out and back in after gpio group add.

### Python setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure
```bash
cp .env.example .env
nano .env  # Add API keys
```

### Pair Bluetooth
```bash
bluetoothctl
power on
scan on
pair MAC_ADDRESS
trust MAC_ADDRESS
connect MAC_ADDRESS
exit
```

### Test
```bash
python3 examples/test_audio.py
```

## Troubleshooting
- GPIO errors: Check gpio group membership
- Audio issues: Verify PipeWire status
- Bluetooth drops: Check power supply (need 27W for Pi 5)

See docs/TROUBLESHOOTING.md for more help.
