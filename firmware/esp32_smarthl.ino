// smartHL ESP32 prototype - PubSubClient
// Topic: smarthl/{MQTT_ID}/up/telemetry , smarthl/{MQTT_ID}/down/cmd
// Isi MQTT_ID, WIFI_SSID, WIFI_PASS, BROKER_IP (192.168.18.107 / 100.64.0.10 via tailscale)
#include <WiFi.h>
#include <PubSubClient.h>

const char* WIFI_SSID = "GANTI_SSID";
const char* WIFI_PASS = "GANTI_PASS";
const char* BROKER_IP = "192.168.18.107";
const int BROKER_PORT = 1883;
const char* MQTT_ID = "shl-demo01";

#define LED_PIN 2

WiFiClient espClient;
PubSubClient mqtt(espClient);
char TOP_UP[64], TOP_DOWN[64], TOP_STATUS[64];

void callback(char* topic, byte* payload, unsigned int len) {
  String msg;
  for (unsigned i = 0; i < len; i++) msg += (char)payload[i];
  // ekspektasi {"led":1} atau {"led":0}
  int led = (msg.indexOf("\"led\":1") >= 0 || msg.indexOf(":1") >= 0) ? 1 : 0;
  if (msg.indexOf("led") >= 0) {
    digitalWrite(LED_PIN, led ? HIGH : LOW);
  }
}

void reconnect() {
  while (!mqtt.connected()) {
    String willTopic = String(TOP_STATUS);
    if (mqtt.connect(MQTT_ID, willTopic.c_str(), 1, true, "offline")) {
      mqtt.subscribe(TOP_DOWN);
      mqtt.publish(TOP_STATUS, "online", true);
    } else {
      delay(2000);
    }
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  Serial.begin(115200);
  snprintf(TOP_UP, sizeof(TOP_UP), "smarthl/%s/up/telemetry", MQTT_ID);
  snprintf(TOP_DOWN, sizeof(TOP_DOWN), "smarthl/%s/down/cmd", MQTT_ID);
  snprintf(TOP_STATUS, sizeof(TOP_STATUS), "smarthl/%s/up/status", MQTT_ID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(500);
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
    float temp = 26.0 + random(-20, 50) / 10.0;  // ganti DHT22
    float hum = 60.0 + random(-100, 100) / 10.0;
    int ledState = digitalRead(LED_PIN);
    char buf[128];
    snprintf(buf, sizeof(buf), "{\"temp\":%.1f,\"hum\":%.1f,\"led\":%d,\"rssi\":%d}",
             temp, hum, ledState, WiFi.RSSI());
    mqtt.publish(TOP_UP, buf);
  }
}
