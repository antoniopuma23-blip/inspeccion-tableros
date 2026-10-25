/*
 * Firmware de EJEMPLO para el ESP32-S3 opcional del robot de inspección.
 *
 * El robot funciona completo SIN este módulo. Si se habilita
 * (esp32.habilitado: true en config.yaml o desde la página Configuración),
 * la Raspberry Pi intercambia mensajes JSON por línea a 115200 baud:
 *
 *   ESP32 -> Pi : {"t":"bateria","v":7.62}   cada 1 s (divisor resistivo)
 *                 {"t":"paro"}               al presionar el botón físico
 *                 {"t":"latido"}             cada 1 s
 *   Pi -> ESP32 : {"t":"estado","paro":false,"v_izq":0.1,"v_der":0.1}
 *                 {"t":"paro"}
 *
 * Conexiones:
 *   ESP32 TX (GPIO17) -> Pi RX (GPIO15)
 *   ESP32 RX (GPIO18) <- Pi TX (GPIO14)
 *   GND común
 *   Batería 2S -> divisor 10k/4.7k -> GPIO4 (ADC). 8.4 V -> ~2.69 V
 *   Botón de paro (NC a GND) -> GPIO5 con pull-up interno
 *   LED de estado -> GPIO2
 */

#include <Arduino.h>

constexpr int PIN_TX = 17;
constexpr int PIN_RX = 18;
constexpr int PIN_ADC_BATERIA = 4;
constexpr int PIN_BOTON_PARO = 5;
constexpr int PIN_LED = 2;
constexpr float FACTOR_DIVISOR = (10.0f + 4.7f) / 4.7f;

HardwareSerial PiSerial(1);
String linea;
bool paroActivo = false;
unsigned long ultimoEnvio = 0;
bool botonPrevio = false;

float leerBateria() {
  uint32_t suma = 0;
  for (int i = 0; i < 16; i++) suma += analogReadMilliVolts(PIN_ADC_BATERIA);
  return (suma / 16.0f) / 1000.0f * FACTOR_DIVISOR;
}

void procesar(const String &msg) {
  // Analizador mínimo: basta con buscar el tipo de mensaje
  if (msg.indexOf("\"t\":\"paro\"") >= 0) {
    paroActivo = true;
  } else if (msg.indexOf("\"t\":\"estado\"") >= 0) {
    paroActivo = msg.indexOf("\"paro\":true") >= 0;
  }
}

void setup() {
  pinMode(PIN_BOTON_PARO, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  analogReadResolution(12);
  PiSerial.begin(115200, SERIAL_8N1, PIN_RX, PIN_TX);
}

void loop() {
  while (PiSerial.available()) {
    char c = PiSerial.read();
    if (c == '\n') {
      procesar(linea);
      linea = "";
    } else if (linea.length() < 256) {
      linea += c;
    }
  }

  // Botón NC: al abrirse el circuito la entrada sube a HIGH -> paro
  bool boton = digitalRead(PIN_BOTON_PARO) == HIGH;
  if (boton && !botonPrevio) PiSerial.println("{\"t\":\"paro\"}");
  botonPrevio = boton;

  if (millis() - ultimoEnvio >= 1000) {
    ultimoEnvio = millis();
    PiSerial.printf("{\"t\":\"bateria\",\"v\":%.2f}\n", leerBateria());
    PiSerial.println("{\"t\":\"latido\"}");
  }

  // LED: parpadeo rápido en paro, lento en operación normal
  digitalWrite(PIN_LED, (millis() / (paroActivo ? 120 : 600)) % 2);
}
