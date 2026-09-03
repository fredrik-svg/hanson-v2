#!/bin/bash
# restart-kiosk.sh — Pålitlig, kontrollerad omstart av kiosk-Chromium.
#
# Använd DENNA istället för manuellt pkill när du vill ladda om sidan
# efter en filändring. Dödar ENDAST vår specifika kiosk-instans (matchat
# på user-data-dir, aldrig ett brett "chromium"-mönster som riskerar att
# träffa övervakningsloopen eller andra processer av misstag), och
# startar om den direkt utan att förlita sig på att bakgrundsövervakaren
# hinner reagera.
#
# Körs som: bash ~/restart-kiosk.sh   (eller ~/hanson-web/restart-kiosk.sh)

set -e

echo "[restart-kiosk] Dödar befintlig kiosk-Chromium (om någon)…"
# Matchar ENDAST processer vars kommandorad innehåller vår unika
# user-data-dir-sökväg — kan aldrig träffa övervakningsloopen (som är
# ett bash-skript, inte en chromium-binär) eller andra orelaterade
# processer.
pkill -9 -f "chromium-kiosk" 2>/dev/null || true
sleep 2

echo "[restart-kiosk] Startar kiosk-Chromium…"
DISPLAY=:0 chromium \
  --no-sandbox \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  --check-for-update-interval=31536000 \
  --autoplay-policy=no-user-gesture-required \
  --user-data-dir=/home/entre/.config/chromium-kiosk \
  --no-first-run \
  http://localhost:8080/ \
  > /tmp/chromium-kiosk.log 2>&1 &

sleep 2
if pgrep -f "chromium-kiosk" > /dev/null; then
    echo "[restart-kiosk] Klart — Chromium kör (PID $(pgrep -f 'chromium-kiosk' | head -1))."
else
    echo "[restart-kiosk] VARNING: Chromium verkar inte ha startat. Kolla /tmp/chromium-kiosk.log"
fi
