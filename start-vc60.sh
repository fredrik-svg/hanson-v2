#!/bin/bash
# start-vc60.sh — Startwrapper för Hanson-agenten på VC60.
#
# Sätter ALLTID rätt ljudvolymer innan agenten startar, så vi slipper
# gissa/komma ihåg manuellt efter varje omstart eller PipeWire-reload.
# Motsvarar start-hanson.sh på Hanson-Pi:n, anpassad för VC60:ans
# identiska USB-ljudhårdvara (samma ReSpeaker + Jieli UACDemo).
#
# BEKRÄFTAD BASLINJE (2026-08-14): UACDemo på 75% gav entré-hörbar volym
# UTAN eko — VC60:ans extra kraft/USB-timing klarade det Pi:n inte klarade
# på samma nivå. Ändra INTE denna volym utan att om-testa eko noggrant.

set -e
cd "$(dirname "$0")"

echo "[start-vc60] Väntar på att echo-cancel-enheter ska bli tillgängliga…"
for i in $(seq 1 20); do
    if pactl list short sinks | grep -q "hanson_echo_cancelled_speaker"; then
        break
    fi
    sleep 0.5
done

# ── Echo-cancel-sinkens volym (den virtuella enheten agenten spelar till) ──
EC_SINK_ID=$(wpctl status | grep -i "hanson_echo_cancelled_speaker" | grep -oE '^\s*│?\s*[0-9]+' | grep -oE '[0-9]+' | head -1)
if [ -n "$EC_SINK_ID" ]; then
    wpctl set-volume "$EC_SINK_ID" 1.0 || echo "VARNING: kunde inte sätta volym på echo-cancel-sink"
    wpctl set-mute "$EC_SINK_ID" 0 2>/dev/null || true
    echo "[start-vc60] Echo-cancel-sink (ID $EC_SINK_ID) satt till full volym."
fi

# ── USB-högtalarens (UACDemo/Jieli) volym — BEKRÄFTAT 75% funkar utan eko ──
pactl set-sink-volume alsa_output.usb-Jieli_Technology_UACDemoV1.0_4150344535343812-00.analog-stereo 75% 2>/dev/null || true
echo "[start-vc60] UACDemo-högtalare satt till 75%."

# ── ReSpeaker-MIKROFONENS indata-nivå — full känslighet, annars trög/tyst ──
# (Detta är SOURCE-volymen, dvs mikrofonens känslighet — separat från
# ReSpeakerns egna SINK-volymer ovan, som inte används för inspelning.)
pactl set-source-volume alsa_input.usb-SEEED_ReSpeaker_4_Mic_Array__UAC1.0_-00.analog-surround-21 100% 2>/dev/null || true
echo "[start-vc60] ReSpeaker mikrofon-indata satt till 100%."

# ── Default-enheter (bör redan vara satta, men säkerställ det ändå) ────────
pactl set-default-sink hanson_echo_cancelled_speaker 2>/dev/null || true
pactl set-default-source hanson_echo_cancelled_mic 2>/dev/null || true

echo "[start-vc60] Startar agent.py…"
exec ./venv/bin/python3 agent.py
