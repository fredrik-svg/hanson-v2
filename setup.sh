#!/bin/bash

# Hanson - ElevenLabs Agent Setup Script
# For Debian Trixie on Raspberry Pi 5

set -e

echo "========================================="
echo "Hanson - ElevenLabs Agent Setup"
echo "========================================="
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check hardware
check_hardware() {
    echo "Kontrollerar hårdvara..."
    if ! grep -q "Raspberry Pi 5" /proc/cpuinfo 2>/dev/null; then
        echo -e "${YELLOW}Varning: Detta script är optimerat för Raspberry Pi 5${NC}"
        read -p "Fortsätt ändå? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

# Create .env.example if missing
create_env_example() {
    if [ ! -f .env.example ]; then
        echo "Skapar .env.example..."
        cat > .env.example << 'ENVEOF'
# ElevenLabs API Configuration
ELEVENLABS_API_KEY=your_api_key_here
ELEVENLABS_AGENT_ID=your_agent_id_here
ENVEOF
    fi
}

# Update system
update_system() {
    echo -e "${GREEN}Uppdaterar system...${NC}"
    sudo apt update
    echo "Systemuppdatering klar."
}

# Install dependencies
install_dependencies() {
    echo -e "${GREEN}Installerar systempaket...${NC}"
    sudo apt install -y \
        python3-pip \
        python3-venv \
        python3-lgpio \
        python3-pyaudio \
        pipewire \
        pipewire-pulse \
        pipewire-alsa \
        wireplumber \
        bluez \
        bluez-tools \
        libportaudio2 \
        portaudio19-dev \
        git \
        curl
    
    echo -e "${GREEN}Systempaket installerade.${NC}"
}

# Setup PipeWire
setup_pipewire() {
    echo -e "${GREEN}Konfigurerar PipeWire...${NC}"
    systemctl --user enable pipewire pipewire-pulse wireplumber 2>/dev/null || true
    systemctl --user start pipewire pipewire-pulse wireplumber 2>/dev/null || true
    echo -e "${GREEN}PipeWire konfigurerad.${NC}"
}

# Setup Bluetooth
setup_bluetooth() {
    echo -e "${GREEN}Aktiverar Bluetooth...${NC}"
    sudo systemctl enable bluetooth
    sudo systemctl start bluetooth
    echo -e "${GREEN}Bluetooth aktiverad.${NC}"
}

# GPIO permissions
setup_gpio() {
    echo -e "${GREEN}Ställer in GPIO-rättigheter...${NC}"
    if ! groups $USER | grep -q gpio; then
        sudo usermod -a -G gpio $USER
        echo -e "${YELLOW}Lagt till $USER i gpio-gruppen. Logga ut och in igen.${NC}"
    else
        echo "Användare redan i gpio-gruppen."
    fi
}

# Python venv
setup_python() {
    echo -e "${GREEN}Skapar Python virtual environment...${NC}"
    python3 -m venv --system-site-packages venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    deactivate
    echo -e "${GREEN}Python-miljö skapad.${NC}"
}

# Create .env
setup_env() {
    if [ ! -f .env ]; then
        echo -e "${GREEN}Skapar .env-fil...${NC}"
        cp .env.example .env
        echo -e "${YELLOW}Redigera .env och lägg till dina API-nycklar:${NC}"
        echo "  nano .env"
    else
        echo -e "${YELLOW}.env finns redan.${NC}"
    fi
}

# Create systemd directory
create_systemd_dir() {
    if [ ! -d systemd ]; then
        mkdir systemd
    fi
}

# Test audio
test_audio() {
    echo -e "${GREEN}Kontrollerar ljudenheter...${NC}"
    echo "=== Inspelningsenheter ==="
    arecord -l || true
    echo ""
    echo "=== Uppspelningsenheter ==="
    aplay -l || true
    echo ""
}

# Main
main() {
    check_hardware
    create_env_example
    update_system
    install_dependencies
    setup_pipewire
    setup_bluetooth
    setup_gpio
    setup_python
    setup_env
    create_systemd_dir
    test_audio
    
    echo ""
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}Setup klar!${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo ""
    echo "Nästa steg:"
    echo "1. Redigera .env:"
    echo "   nano .env"
    echo ""
    echo "2. Para Bluetooth-högtalare:"
    echo "   bluetoothctl"
    echo "   > power on"
    echo "   > scan on"
    echo "   > pair XX:XX:XX:XX:XX:XX"
    echo "   > trust XX:XX:XX:XX:XX:XX"
    echo "   > connect XX:XX:XX:XX:XX:XX"
    echo ""
    echo "3. Koppla hårdvara:"
    echo "   - WS2812B LED Ring DI → GPIO 18"
    echo "   - LED Ring 5V → 5V, GND → GND"
    echo "   - Knapp → GPIO 17 och GND"
    echo ""
    echo "4. Kör agenten:"
    echo "   source venv/bin/activate"
    echo "   python3 agent.py"
    echo ""
    echo -e "${YELLOW}OBS: Om du lades till i gpio-gruppen, logga ut och in igen först.${NC}"
}

main
