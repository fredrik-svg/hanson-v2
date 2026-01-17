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

### Policy Violation Error (first_message override)
If you see:
```
Error: received 1008 (policy violation) Override for field 'first_message' is not allowed by config.
```

**Solution:**
1. Go to [ElevenLabs Dashboard](https://elevenlabs.io/app/conversational-ai)
2. Select your agent
3. Under **Prompt** → **First Message**:
   - Leave empty for silent start (agent waits for user)
   - Or set your preferred greeting message
4. Save changes

**Note:** The `first_message` field cannot be overridden from code unless explicitly enabled in your agent's security settings. It's easier to configure it directly in the dashboard.

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
