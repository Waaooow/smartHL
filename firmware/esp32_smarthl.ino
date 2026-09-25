// smartHL — contoh firmware ESP32 (Arduino IDE / PlatformIO)
// Protokol: sama persis dengan virtual_esp8266.py & dashboard.
//   UP   smarthl/{MQTT_ID}/up/telemetry  -> {"temp":..,"hum":..,"led":..,"relay1":..,"rssi":..}
//   UP   smarthl/{MQTT_ID}/up/status     -> online/offline (retain + LWT)
//   DOWN smarthl/{MQTT_ID}/down/cmd      -> {"led":1} / {"relay1":0} / ...
//
// Lib yang dibutuhkan (Arduino IDE > Sketch > Include Library > Manage):
//   - PubSubClient by Nick O'Leary
// Tanpa lib JSON tambahan: parser angka Diekstrak manual (cukup untuk {key:angka}).
//
// Wiring contoh:
//   LED built-in  -> GPIO2 (sebagian board menyala saat HIGH)
//   Relay 1 (pompa/lampu) -> GPIO5  (modul relay aktif-LOW? balik logikanya di setOutput)
//   Relay 2               -> GPIO18

#include <WiFi.h>
#include <PubSubClient.h>

// ====== KONFIGURASI — GANTI INI DULU ======
const char* WIFI_SSID  = "GANTI_SSID";
const char* WIFI_PASS  = "GANTI_PASS";
const char* BROKER_IP  = "192.168.18.107";  // IP VM broker (atau IP publik/IPv6 server prod)
const int   BROKER_PORT = 1883;
const char* MQTT_ID    = "shl-demo01";       // HARUS sama dengan MQTT ID di dashboard!
const char* MQTT_TOKEN = "GANTI_TOKEN";     // kolom Token di halaman Perangkat (auth broker)
// =========================================

#define LED_PIN    2
#define RELAY1_PIN 5
#define RELAY2_PIN 18

WiFiClient espClient;
PubSubClient mqtt(espClient);
char TOP_UP[64], TOP_DOWN[64], TOP_STATUS[64];

// State logis: 1 = ON, 0 = OFF (dashboard membaca nilai ini balik via telemetri)
int out_led = 0, out_relay1 = 0, out_relay2 = 0;

void applyOutputs() {
  digitalWrite(LED_PIN, out_led ? HIGH : LOW);
  digitalWrite(RELAY1_PIN, out_relay1 ? HIGH : LOW);
  digitalWrite(RELAY2_PIN, out_relay2 ? HIGH : LOW);
}

// Parser minimal JSON {"nama":angka, ...} -> panggil act() per pasangan.
// Mengabaikan string/spasi; hanya angka yang diproses.
void handleCmd(char* msg) {
  char* p = msg;
  while ((p = strchr(p, '"')) != NULL) {
    char* k0 = ++p;
    char* k1 = strchr(p, '"');
    if (!k1) break;
    *k1 = '\0';
    char* colon = strchr(k1 + 1, ':');
    if (!colon) break;
    float v = atof(colon + 1);
    int on = (v != 0);
    if (strcmp(k0, "led") == 0) out_led = on;
    else if (strcmp(k0, "relay1") == 0) out_relay1 = on;
    else if (strcmp(k0, "relay2") == 0) out_relay2 = on;
    // tambah: else if (strcmp(k0, "relay3")==0) ...
    Serial.printf("cmd %s=%d\n", k0, on);
    p = colon + 1;
  }
  applyOutputs();
}

void callback(char* topic, byte* payload, unsigned int len) {
  static char buf[256];
  if (len >= sizeof(buf)) len = sizeof(buf) - 1;
  memcpy(buf, payload, len);
  buf[len] = '\0';
  Serial.printf("down [%s]: %s\n", topic, buf);
  handleCmd(buf);
}

void reconnect() {
  while (!mqtt.connected()) {
    Serial.print("MQTT connect...");
    if (mqtt.connect(MQTT_ID, MQTT_TOKEN, TOP_STATUS, 1, true, "offline")) {
      Serial.println("ok");
      mqtt.subscribe(TOP_DOWN);
      mqtt.publish(TOP_STATUS, "online", true);
    } else {
      Serial.printf("gagal rc=%d, coba lagi 2 dtk\n", mqtt.state());
      delay(2000);
    }
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  pinMode(RELAY1_PIN, OUTPUT);
  pinMode(RELAY2_PIN, OUTPUT);
  applyOutputs();
  Serial.begin(115200);
  snprintf(TOP_UP, sizeof(TOP_UP), "smarthl/%s/up/telemetry", MQTT_ID);
  snprintf(TOP_DOWN, sizeof(TOP_DOWN), "smarthl/%s/down/cmd", MQTT_ID);
  snprintf(TOP_STATUS, sizeof(TOP_STATUS), "smarthl/%s/up/status", MQTT_ID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi...");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println(WiFi.localIP());
  mqtt.setServer(BROKER_IP, BROKER_PORT);
  mqtt.setCallback(callback);
  mqtt.setBufferSize(512);
}

void loop() {
  if (!mqtt.connected()) reconnect();
  mqtt.loop();
  static unsigned long last = 0;
  if (millis() - last > 5000) {
    last = millis();
    // TODO: ganti 2 baris ini dengan DHT22 asli (lib DHT sensor library):
    //   float temp = dht.readTemperature(); float hum = dht.readHumidity();
    float temp = 26.0 + random(-20, 50) / 10.0;
    float hum  = 60.0 + random(-100, 100) / 10.0;
    char buf[192];
    snprintf(buf, sizeof(buf),
             "{\"temp\":%.1f,\"hum\":%.1f,\"led\":%d,\"relay1\":%d,\"relay2\":%d,\"rssi\":%d}",
             temp, hum, out_led, out_relay1, out_relay2, WiFi.RSSI());
    mqtt.publish(TOP_UP, buf);
    Serial.println(buf);
  }
}
