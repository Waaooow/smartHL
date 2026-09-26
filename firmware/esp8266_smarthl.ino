// smartHL — contoh firmware ESP8266 (NodeMCU / Wemos D1 mini)
// Protokol IDENTIK dengan versi ESP32, jadi dashboard tidak perlu diubah.
//   UP   smarthl/{MQTT_ID}/up/telemetry
//   DOWN smarthl/{MQTT_ID}/down/cmd
//
// Lib (Arduino IDE > Manage Libraries): PubSubClient by Nick O'Leary.
// Board: "NodeMCU 1.0 (ESP-12E Module)" / "LOLIN(WEMOS) D1 R2 & mini".
//
// PERHATIAN KHUSUS ESP8266:
//   - LED_BUILTIN (GPIO2) aktif-LOW: digitalWrite(LOW)=menyala. Kode di bawah
//     sudah membalik, jadi dashboard led=1 tetap berarti "menyala".
//   - Pin yang aman untuk relay: D1 (GPIO5), D2 (GPIO4). Hindari GPIO0/2/15
//     untuk output berat saat boot.
//   - A0 hanya 0-1V (NodeMCU sudah ada pembagi tegangan, aman untuk LDR).
//
// Wiring contoh:
//   Relay 1 -> D1 (GPIO5)
//   LDR (cahaya) -> A0 (+ 10k ke GND sebagai pembagi)

#include <ESP8266WiFi.h>
#include <PubSubClient.h>

// ====== KONFIGURASI — GANTI INI DULU ======
const char* WIFI_SSID   = "GANTI_SSID";
const char* WIFI_PASS   = "GANTI_PASS";
const char* BROKER_IP   = "192.168.18.107";  // IP VM broker
const int   BROKER_PORT = 1883;
const char* MQTT_ID     = "shl-esp01";        // HARUS sama dengan MQTT ID di dashboard!
const char* MQTT_TOKEN  = "GANTI_TOKEN";      // kolom Token di halaman Perangkat (auth broker)
// =========================================

#define RELAY1_PIN 5  // D1

WiFiClient espClient;
PubSubClient mqtt(espClient);
char TOP_UP[64], TOP_DOWN[64], TOP_STATUS[64];

int out_led = 0, out_relay1 = 0;

void applyOutputs() {
  digitalWrite(LED_BUILTIN, out_led ? LOW : HIGH);  // aktif-LOW!
  digitalWrite(RELAY1_PIN, out_relay1 ? HIGH : LOW);
}

void handleCmd(char* msg) {
  char* p = msg;
  while ((p = strchr(p, '"')) != NULL) {
    char* k0 = ++p;
    char* k1 = strchr(p, '"');
    if (!k1) break;
    *k1 = '\0';
    char* colon = strchr(k1 + 1, ':');
    if (!colon) break;
    int on = (atof(colon + 1) != 0);
    if (strcmp(k0, "led") == 0) out_led = on;
    else if (strcmp(k0, "relay1") == 0) out_relay1 = on;
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
    if (mqtt.connect(MQTT_ID, MQTT_ID, MQTT_TOKEN, TOP_STATUS, 1, true, "offline", true)) {
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
  pinMode(LED_BUILTIN, OUTPUT);
  pinMode(RELAY1_PIN, OUTPUT);
  applyOutputs();
  Serial.begin(115200);
  snprintf(TOP_UP, sizeof(TOP_UP), "smarthl/%s/up/telemetry", MQTT_ID);
  snprintf(TOP_DOWN, sizeof(TOP_DOWN), "smarthl/%s/down/cmd", MQTT_ID);
  snprintf(TOP_STATUS, sizeof(TOP_STATUS), "smarthl/%s/up/status", MQTT_ID);
  WiFi.mode(WIFI_STA);
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
    // Sensor sungguhan yang murah: LDR di A0 (0-100%).
    // Suhu/humiditas di bawah ini simulasi — pasang DHT22 + lib "DHT sensor
    // library" kalau mau asli, lalu ganti 2 baris temp/hum.
    int light = map(analogRead(A0), 0, 1023, 0, 100);
    float temp = 26.0 + random(-20, 50) / 10.0;
    float hum  = 60.0 + random(-100, 100) / 10.0;
    char buf[192];
    snprintf(buf, sizeof(buf),
             "{\"temp\":%.1f,\"hum\":%.1f,\"light\":%d,\"led\":%d,\"relay1\":%d,\"rssi\":%d}",
             temp, hum, light, out_led, out_relay1, WiFi.RSSI());
    mqtt.publish(TOP_UP, buf);
    Serial.println(buf);
  }
}
