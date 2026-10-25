#!/usr/bin/env bash
# =============================================================================
#  Instalador del robot de inspección térmica para Raspberry Pi 3 Model B+
#  (Raspberry Pi OS Lite 64 bits "Bookworm", Python 3.11)
#
#  Qué hace:
#    1. Instala dependencias del sistema (OpenCV, NumPy y picamera2 desde apt)
#    2. Instala pigpio (desde apt o compilándolo si no está disponible)
#    3. Habilita I2C a 400 kHz, UART, cámara y desactiva el audio analógico
#       (comparte periférico con el PWM por hardware de GPIO18/GPIO13)
#    4. Crea un entorno virtual de Python e instala requirements.txt
#    5. Habilita pigpiod y crea el servicio systemd robot-inspeccion
#    6. Cambia el hostname a "robot" y habilita mDNS (http://robot.local:5000)
#
#  Uso (desde la carpeta del proyecto):
#      sudo bash scripts/instalar_pi.sh
# =============================================================================
set -euo pipefail

VERDE='\033[1;32m'; AMARILLO='\033[1;33m'; ROJO='\033[1;31m'; NC='\033[0m'
paso()  { echo -e "\n${VERDE}==> $*${NC}"; }
aviso() { echo -e "${AMARILLO}AVISO: $*${NC}"; }
error() { echo -e "${ROJO}ERROR: $*${NC}" >&2; exit 1; }

[[ $EUID -eq 0 ]] || error "Ejecuta este script con sudo: sudo bash scripts/instalar_pi.sh"

PROYECTO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USUARIO="${SUDO_USER:-pi}"
HOSTNAME_ROBOT="robot"
SERVICIO="robot-inspeccion"
VENV="$PROYECTO/.venv"

# Archivo de configuración de arranque (Bookworm usa /boot/firmware)
if [[ -f /boot/firmware/config.txt ]]; then BOOT_CFG=/boot/firmware/config.txt; else BOOT_CFG=/boot/config.txt; fi

echo "Proyecto: $PROYECTO"
echo "Usuario:  $USUARIO"
echo "Boot:     $BOOT_CFG"

# -----------------------------------------------------------------------------
paso "1/6 Instalando paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip python3-dev \
    python3-numpy python3-opencv python3-picamera2 python3-libcamera \
    python3-smbus i2c-tools \
    fonts-dejavu-core avahi-daemon git build-essential wget unzip
# Herramientas de cámara (el nombre del paquete cambió entre versiones)
apt-get install -y --no-install-recommends rpicam-apps-lite 2>/dev/null \
    || apt-get install -y --no-install-recommends libcamera-apps-lite 2>/dev/null \
    || aviso "No se instalaron las herramientas rpicam (opcional)"

# -----------------------------------------------------------------------------
paso "2/6 Instalando pigpio"
if apt-get install -y pigpio python3-pigpio 2>/dev/null; then
    echo "pigpio instalado desde apt"
elif command -v pigpiod >/dev/null 2>&1; then
    echo "pigpiod ya estaba instalado"
else
    aviso "pigpio no está en los repositorios de esta versión; se compilará desde el código fuente"
    TMP=$(mktemp -d)
    wget -q -O "$TMP/pigpio.zip" https://github.com/joan2937/pigpio/archive/refs/tags/v79.zip
    unzip -q "$TMP/pigpio.zip" -d "$TMP"
    make -C "$TMP/pigpio-79" -j"$(nproc)"
    make -C "$TMP/pigpio-79" install
    ldconfig
    rm -rf "$TMP"
fi
PIGPIOD_BIN="$(command -v pigpiod || echo /usr/local/bin/pigpiod)"

# Servicio de pigpiod (solo escucha en localhost por seguridad)
cat > /etc/systemd/system/pigpiod.service <<EOF
[Unit]
Description=Demonio pigpio (PWM por hardware y lectura de encoders)
After=network.target

[Service]
Type=forking
ExecStart=$PIGPIOD_BIN -l -s 5
ExecStop=/bin/systemctl kill pigpiod
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

# -----------------------------------------------------------------------------
paso "3/6 Configurando interfaces (I2C, UART, cámara, audio)"
if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_i2c 0            # habilitar I2C
    raspi-config nonint do_serial_hw 0      # habilitar UART (para el ESP32 opcional)
    raspi-config nonint do_serial_cons 1    # desactivar la consola por UART
else
    aviso "raspi-config no encontrado; se edita $BOOT_CFG directamente"
fi
agregar_boot() {
    local linea="$1" clave="${1%%=*}"
    if grep -qE "^\s*${clave}=" "$BOOT_CFG"; then
        sed -i -E "s|^\s*${clave}=.*|${linea}|" "$BOOT_CFG"
    else
        echo "$linea" >> "$BOOT_CFG"
    fi
}
agregar_boot "dtparam=i2c_arm=on"
agregar_boot "dtparam=i2c_arm_baudrate=400000"   # MLX90640 necesita >= 400 kHz para 4 Hz
agregar_boot "dtparam=audio=off"                 # libera el PWM por hardware
agregar_boot "enable_uart=1"
agregar_boot "camera_auto_detect=1"
modprobe i2c-dev || true
grep -q "^i2c-dev" /etc/modules || echo "i2c-dev" >> /etc/modules
usermod -aG i2c,gpio,video,dialout "$USUARIO" || true

# -----------------------------------------------------------------------------
paso "4/6 Creando entorno virtual de Python e instalando dependencias"
# --system-site-packages permite usar OpenCV, NumPy y picamera2 de apt
# (precompilados para ARM: evita compilar por horas en la Pi 3).
sudo -u "$USUARIO" python3 -m venv --system-site-packages "$VENV"
sudo -u "$USUARIO" "$VENV/bin/pip" install --upgrade pip wheel
sudo -u "$USUARIO" "$VENV/bin/pip" install -r "$PROYECTO/requirements.txt"
mkdir -p "$PROYECTO/data"
chown -R "$USUARIO":"$USUARIO" "$PROYECTO/data" "$VENV"

# -----------------------------------------------------------------------------
paso "5/6 Creando servicio systemd ($SERVICIO)"
sed -e "s|@USUARIO@|$USUARIO|g" -e "s|@PROYECTO@|$PROYECTO|g" -e "s|@VENV@|$VENV|g" \
    "$PROYECTO/scripts/robot-inspeccion.service" > "/etc/systemd/system/$SERVICIO.service"
systemctl daemon-reload
systemctl enable pigpiod.service
systemctl restart pigpiod.service
systemctl enable "$SERVICIO.service"

# -----------------------------------------------------------------------------
paso "6/6 Configurando hostname '$HOSTNAME_ROBOT' (http://$HOSTNAME_ROBOT.local:5000)"
ACTUAL="$(hostname)"
if [[ "$ACTUAL" != "$HOSTNAME_ROBOT" ]]; then
    hostnamectl set-hostname "$HOSTNAME_ROBOT"
    if grep -q "127.0.1.1" /etc/hosts; then
        sed -i -E "s/^127\.0\.1\.1\s+.*/127.0.1.1\t$HOSTNAME_ROBOT/" /etc/hosts
    else
        echo -e "127.0.1.1\t$HOSTNAME_ROBOT" >> /etc/hosts
    fi
fi
systemctl enable --now avahi-daemon

echo -e "\n${VERDE}Instalación completa.${NC}"
echo "  * Reinicia para aplicar I2C/UART/audio:   sudo reboot"
echo "  * Después del reinicio el robot arranca solo. Abre:  http://$HOSTNAME_ROBOT.local:5000"
echo "  * Ver registros:      journalctl -u $SERVICIO -f"
echo "  * Probar componentes: $VENV/bin/python scripts/pruebas/probar_motores.py (y los demás)"
