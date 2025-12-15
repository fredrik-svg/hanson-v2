# Troubleshooting

## GPIO Issues

### Permission denied
```bash
sudo usermod -a -G gpio $USER
# Log out and back in
```

### lgpio not found
```bash
sudo apt install python3-lgpio
```

## Audio Issues

### ReSpeaker not detected
```bash
lsusb | grep Seeed  # Check USB
arecord -l  # Check ALSA
```
Try different USB port (use blue USB 3.0 ports).

### Bluetooth no audio
```bash
# Reconnect
bluetoothctl connect MAC

# Set as default
pactl set-default-sink bluez_sink.XX_XX_XX_XX_XX_XX.a2dp_sink

# Restart PipeWire
systemctl --user restart pipewire-pulse
```

### Audio lag
Edit agent.py:
```python
CHUNK_SIZE = 2048  # Try 512, 1024, 2048, 4096
```

## ElevenLabs API

### Invalid API key
- Check .env file
- No spaces/quotes around key
- Verify on elevenlabs.io dashboard

### Connection timeout
```bash
ping api.elevenlabs.io
```

## Button Issues

### Not responding
- Check wiring: GPIO 17 to button to GND
- Test with multimeter
- Try different GPIO pin

### Multiple triggers (bouncing)
Increase debounce delay in code.

## Service Issues

### Won't start
```bash
sudo systemctl status elevenlabs-agent
sudo journalctl -u elevenlabs-agent -n 50
```

Check paths in service file match your installation.

## Diagnostics
```bash
# System info
uname -a

# GPIO
sudo gpioinfo

# Audio
arecord -l
pactl list sinks

# Bluetooth
systemctl status bluetooth

# Python
source venv/bin/activate
pip list
```
