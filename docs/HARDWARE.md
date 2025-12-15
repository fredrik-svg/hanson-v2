# Hardware Setup

## Wiring

### LED
```
GPIO 18 (Pin 12) → LED Anode (+)
LED Cathode (-) → 220Ω Resistor → GND (Pin 14)
```

### Button
```
GPIO 17 (Pin 11) → Button → GND (Pin 9)
```
Uses internal pull-up, no external resistor needed.

## GPIO Pinout (BCM)
```
GPIO17 (11) (12) GPIO18
GPIO27 (13) (14) GND
```

## Testing

### LED Test
```python
import lgpio as GPIO
import time
chip = GPIO.gpiochip_open(4)
GPIO.gpio_claim_output(chip, 18, 0)
for i in range(5):
    GPIO.gpio_write(chip, 18, 1)
    time.sleep(0.5)
    GPIO.gpio_write(chip, 18, 0)
    time.sleep(0.5)
GPIO.gpiochip_close(chip)
```

### Button Test
```python
import lgpio as GPIO
chip = GPIO.gpiochip_open(4)
GPIO.gpio_claim_input(chip, 17, GPIO.SET_PULL_UP)
print("Press button...")
while True:
    if GPIO.gpio_read(chip, 17) == 0:
        print("Pressed!")
```

## Audio Devices

### Verify ReSpeaker
```bash
arecord -l  # Should show ReSpeaker
```

### Verify Bluetooth
```bash
pactl list sinks short
```

## Power
- Use official 27W USB-C power supply
- ReSpeaker draws ~500mA
- Insufficient power causes random reboots
