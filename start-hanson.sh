#!/bin/bash
# Wrapper som körs av systemd före agent.py.
# Säkerställer att PipeWire echo-cancel-enheterna är default varje gång
# tjänsten startar — överlever omstart utan att förlita sig på att
# default-valet sparats någon annanstans.

set -e

# Vänta tills PipeWire faktiskt har skapat de virtuella enheterna
# (echo-cancel-modulen kan ta ett par sekunder efter att PipeWire startat)
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

echo "PipeWire echo-cancel-enheter satta som default. Startar Hanson..."

# Starta agenten (exec ersätter shell-processen så signaler når Python direkt)
exec /home/genio/hanson-v2/venv/bin/python3 /home/genio/hanson-v2/agent.py
