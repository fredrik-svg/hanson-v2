#!/bin/bash
# Wrapper som körs av systemd före agent.py.
# Säkerställer att PipeWire echo-cancel-enheterna är default OCH har full
# volym varje gång tjänsten startar — överlever omstart och skyddar mot att
# t.ex. EasyEffects eller raderat PipeWire-tillstånd lämnar fel default/volym.

set -e

# Vänta tills PipeWire skapat de virtuella echo-cancel-enheterna
for i in $(seq 1 30); do
    if pactl list short sources | grep -q hanson_echo_cancelled_mic; then
        break
    fi
    echo "Väntar på PipeWire echo-cancel-enheter... ($i/30)"
    sleep 1
done

# Sätt eko-cancellerade enheter som default
pactl set-default-source hanson_echo_cancelled_mic || echo "VARNING: kunde inte sätta default source"
pactl set-default-sink   hanson_echo_cancelled_speaker || echo "VARNING: kunde inte sätta default sink"

# Säkerställ full volym på echo-cancel-sinken via wpctl (pactl kan inte sätta
# volym på filter-noder; wpctl gör det). Hittar ID:t dynamiskt via node.name.
EC_SINK_ID=$(wpctl status | grep -i "hanson_echo_cancelled_speaker" | grep -oE '^\s*│?\s*[0-9]+' | grep -oE '[0-9]+' | head -1)
if [ -n "$EC_SINK_ID" ]; then
    wpctl set-volume "$EC_SINK_ID" 1.0 || echo "VARNING: kunde inte sätta volym på echo-cancel-sink"
    wpctl set-mute "$EC_SINK_ID" 0 2>/dev/null || true
    echo "Echo-cancel-sink (ID $EC_SINK_ID) satt till full volym."
else
    echo "VARNING: hittade inte echo-cancel-sinkens ID för volyminställning"
fi

# Säkerställ också att Waveshare-hårdvaran har vettig volym
pactl set-sink-volume alsa_output.usb-Solid_State_System_Co._Ltd._USB_PnP_Audio_Device_000000000000-00.analog-stereo 100% 2>/dev/null || true

echo "PipeWire-routing klar. Startar Hanson..."

exec /home/genio/hanson-v2/venv/bin/python3 /home/genio/hanson-v2/agent.py
