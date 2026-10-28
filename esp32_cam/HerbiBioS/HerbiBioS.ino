#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>
#include <Preferences.h>
#include <cstring>
#include <cstdlib>

#if __has_include(<esp_arduino_version.h>)
#include <esp_arduino_version.h>
#endif

namespace {

constexpr char WIFI_SSID[] = "Garbage";
constexpr char WIFI_PASSWORD[] = "12345678";
constexpr uint32_t SERIAL_BAUD = 115200;

// BIN_1 -> GPIO12, BIN_2 -> GPIO13, BIN_3 -> GPIO14.
// GPIO12 влияет на загрузку ESP32. Если плата не стартует,
// отключи питание и проверь загрузку с отсоединённым сигналом серво.
constexpr int SERVO_PINS[] = {12, 13, 14};
constexpr char *BIN_NAMES[] = {
  const_cast<char *>("BIN_1"),
  const_cast<char *>("BIN_2"),
  const_cast<char *>("BIN_3")
};

constexpr int SERVO_MIN_PULSE_US = 500;
constexpr int SERVO_MAX_PULSE_US = 2400;
constexpr uint32_t AUTO_OPEN_TIME_MS = 3000;
constexpr size_t COMMAND_BUFFER_SIZE = 48;

// AI-Thinker ESP32-CAM camera pin map.
constexpr int CAM_PIN_PWDN = 32;
constexpr int CAM_PIN_RESET = -1;
constexpr int CAM_PIN_XCLK = 0;
constexpr int CAM_PIN_SIOD = 26;
constexpr int CAM_PIN_SIOC = 27;
constexpr int CAM_PIN_Y9 = 35;
constexpr int CAM_PIN_Y8 = 34;
constexpr int CAM_PIN_Y7 = 39;
constexpr int CAM_PIN_Y6 = 36;
constexpr int CAM_PIN_Y5 = 21;
constexpr int CAM_PIN_Y4 = 19;
constexpr int CAM_PIN_Y3 = 18;
constexpr int CAM_PIN_Y2 = 5;
constexpr int CAM_PIN_VSYNC = 25;
constexpr int CAM_PIN_HREF = 23;
constexpr int CAM_PIN_PCLK = 22;

WebServer server(80);
Preferences preferences;
Servo servos[3];

int closedAngles[3] = {0, 0, 0};
int openAngles[3] = {90, 90, 90};
bool servoAttached[3] = {false, false, false};
bool cameraReady = false;

char commandBuffer[COMMAND_BUFFER_SIZE] = {};
size_t commandLength = 0;
bool discardCommand = false;

int activeServo = -1;
uint32_t servoCloseAt = 0;
char activeDoneMessage[COMMAND_BUFFER_SIZE] = {};

void sendText(int statusCode, const char *contentType, const String &body) {
  server.sendHeader("Cache-Control", "no-store");
  server.send(statusCode, contentType, body);
}

int findBinIndex(const char *name) {
  if (name == nullptr) {
    return -1;
  }

  for (int i = 0; i < 3; ++i) {
    if (strcmp(name, BIN_NAMES[i]) == 0) {
      return i;
    }
  }

  return -1;
}

bool parseAngle(const char *text, int &angle) {
  if (text == nullptr || *text == '\0') {
    return false;
  }

  char *end = nullptr;
  const long parsed = strtol(text, &end, 10);
  if (end == text || *end != '\0' || parsed < 0 || parsed > 180) {
    return false;
  }

  angle = static_cast<int>(parsed);
  return true;
}

bool attachServoIfNeeded(int index) {
  if (index < 0 || index >= 3) {
    return false;
  }

  if (servoAttached[index] && servos[index].attached()) {
    return true;
  }

  servos[index].setPeriodHertz(50);
  servos[index].attach(
      SERVO_PINS[index],
      SERVO_MIN_PULSE_US,
      SERVO_MAX_PULSE_US);

  servoAttached[index] = servos[index].attached();
  return servoAttached[index];
}

void handleHealth() {
  String body = "{\"status\":\"";
  body += cameraReady ? "ready" : "camera_error";
  body += "\",\"busy\":";
  body += activeServo >= 0 ? "true" : "false";
  body += ",\"camera_ready\":";
  body += cameraReady ? "true" : "false";
  body += "}";

  sendText(200, "application/json", body);
}

void handleCapture() {
  if (activeServo >= 0) {
    sendText(423, "text/plain", "BUSY");
    return;
  }

  if (!cameraReady) {
    sendText(503, "text/plain", "Camera is not ready");
    return;
  }

  camera_fb_t *frame = esp_camera_fb_get();
  if (frame == nullptr) {
    sendText(503, "text/plain", "Camera capture failed");
    return;
  }

  WiFiClient client = server.client();
  client.printf(
      "HTTP/1.1 200 OK\r\n"
      "Content-Type: image/jpeg\r\n"
      "Content-Length: %u\r\n"
      "Cache-Control: no-store\r\n"
      "Connection: close\r\n\r\n",
      static_cast<unsigned int>(frame->len));
  client.write(frame->buf, frame->len);
  esp_camera_fb_return(frame);
}

void startTimedBin(int index) {
  if (activeServo >= 0) {
    Serial.println("BUSY");
    return;
  }

  if (!attachServoIfNeeded(index)) {
    Serial.println("ERR SERVO_INIT_FAILED");
    return;
  }

  activeServo = index;
  servos[index].write(openAngles[index]);
  servoCloseAt = millis() + AUTO_OPEN_TIME_MS;

  snprintf(
      activeDoneMessage,
      sizeof(activeDoneMessage),
      "DONE %s",
      BIN_NAMES[index]);

  Serial.printf("ACK %s\n", BIN_NAMES[index]);
}

void setBinAngle(int index, int angle, bool moveCommand) {
  if (activeServo >= 0) {
    Serial.println("BUSY");
    return;
  }

  if (!attachServoIfNeeded(index)) {
    Serial.println("ERR SERVO_INIT_FAILED");
    return;
  }

  // This position is held until another angle command is received.
  servos[index].write(angle);

  if (moveCommand) {
    Serial.printf("ACK MOVE %s %d\n", BIN_NAMES[index], angle);
    Serial.printf("DONE MOVE %s %d\n", BIN_NAMES[index], angle);
  } else {
    Serial.printf("OK %s %d\n", BIN_NAMES[index], angle);
  }
}

void processSetCommand(char *savePointer) {
  char *binName = strtok_r(nullptr, " ", &savePointer);
  char *position = strtok_r(nullptr, " ", &savePointer);
  char *angleText = strtok_r(nullptr, " ", &savePointer);
  char *extra = strtok_r(nullptr, " ", &savePointer);

  if (binName == nullptr || position == nullptr ||
      angleText == nullptr || extra != nullptr) {
    Serial.println("ERR SET_FORMAT");
    return;
  }

  const int index = findBinIndex(binName);
  int angle = 0;
  if (index < 0 || !parseAngle(angleText, angle)) {
    Serial.println("ERR SET_VALUE");
    return;
  }

  const bool isOpen = strcmp(position, "OPEN") == 0;
  if (!isOpen && strcmp(position, "CLOSED") != 0) {
    Serial.println("ERR SET_POSITION");
    return;
  }

  if (activeServo >= 0) {
    Serial.println("BUSY");
    return;
  }

  char key[16] = {};
  snprintf(key, sizeof(key), "%s_%d", isOpen ? "open" : "closed", index + 1);

  if (preferences.putUChar(key, static_cast<uint8_t>(angle)) != 1) {
    Serial.println("ERR ANGLE_SAVE_FAILED");
    return;
  }

  if (isOpen) {
    openAngles[index] = angle;
  } else {
    closedAngles[index] = angle;
  }

  Serial.printf(
      "OK SET %s %s %d\n",
      binName,
      position,
      angle);
}

void sendStoredAngles() {
  Serial.printf(
      "ANGLES %d %d %d %d %d %d\n",
      closedAngles[0], openAngles[0],
      closedAngles[1], openAngles[1],
      closedAngles[2], openAngles[2]);
}

void loadServoPositions() {
  preferences.begin("herbibios", false);

  for (int i = 0; i < 3; ++i) {
    char key[16] = {};

    snprintf(key, sizeof(key), "closed_%d", i + 1);
    closedAngles[i] = preferences.getUChar(key, 0);

    snprintf(key, sizeof(key), "open_%d", i + 1);
    openAngles[i] = preferences.getUChar(key, 90);
  }
}

void processCommand(const char *command) {
  char commandCopy[COMMAND_BUFFER_SIZE] = {};
  strlcpy(commandCopy, command, sizeof(commandCopy));

  char *savePointer = nullptr;
  char *operation = strtok_r(commandCopy, " ", &savePointer);
  if (operation == nullptr) {
    return;
  }

  const int index = findBinIndex(operation);
  if (index >= 0) {
    char *angleText = strtok_r(nullptr, " ", &savePointer);
    char *extra = strtok_r(nullptr, " ", &savePointer);

    if (angleText == nullptr) {
      if (extra != nullptr) {
        Serial.println("ERR COMMAND_FORMAT");
        return;
      }
      startTimedBin(index);
      return;
    }

    if (extra != nullptr) {
      Serial.println("ERR COMMAND_FORMAT");
      return;
    }

    int angle = 0;
    if (!parseAngle(angleText, angle)) {
      Serial.println("ERR ANGLE");
      return;
    }

    setBinAngle(index, angle, false);
    return;
  }

  if (strcmp(operation, "MOVE") == 0) {
    char *binName = strtok_r(nullptr, " ", &savePointer);
    char *angleText = strtok_r(nullptr, " ", &savePointer);
    char *extra = strtok_r(nullptr, " ", &savePointer);

    const int moveIndex = findBinIndex(binName);
    int angle = 0;
    if (moveIndex < 0 || angleText == nullptr || extra != nullptr ||
        !parseAngle(angleText, angle)) {
      Serial.println("ERR MOVE_FORMAT");
      return;
    }

    setBinAngle(moveIndex, angle, true);
    return;
  }

  if (strcmp(operation, "SET") == 0) {
    processSetCommand(savePointer);
    return;
  }

  if (strcmp(operation, "GET_ANGLES") == 0) {
    sendStoredAngles();
    return;
  }

  if (strcmp(operation, "UNKNOWN") == 0) {
    Serial.println("ERR UNKNOWN_NOT_SORTED");
    return;
  }

  Serial.println("ERR UNKNOWN_COMMAND");
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    const char incoming = static_cast<char>(Serial.read());

    if (incoming == '\r') {
      continue;
    }

    if (incoming == '\n') {
      if (discardCommand) {
        Serial.println("ERR COMMAND_TOO_LONG");
        discardCommand = false;
      } else {
        commandBuffer[commandLength] = '\0';
        processCommand(commandBuffer);
      }

      commandLength = 0;
      continue;
    }

    if (discardCommand) {
      continue;
    }

    if (commandLength + 1 >= COMMAND_BUFFER_SIZE) {
      commandLength = 0;
      discardCommand = true;
      continue;
    }

    commandBuffer[commandLength++] = incoming;
  }
}

bool startAccessPoint() {
  WiFi.mode(WIFI_AP);
  WiFi.setSleep(false);

  if (!WiFi.softAP(WIFI_SSID, WIFI_PASSWORD, 1, false, 1)) {
    Serial.println("FATAL WIFI_AP_FAILED");
    return false;
  }

  Serial.print("AP_IP ");
  Serial.println(WiFi.softAPIP());
  return true;
}

bool startCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = CAM_PIN_Y2;
  config.pin_d1 = CAM_PIN_Y3;
  config.pin_d2 = CAM_PIN_Y4;
  config.pin_d3 = CAM_PIN_Y5;
  config.pin_d4 = CAM_PIN_Y6;
  config.pin_d5 = CAM_PIN_Y7;
  config.pin_d6 = CAM_PIN_Y8;
  config.pin_d7 = CAM_PIN_Y9;
  config.pin_xclk = CAM_PIN_XCLK;
  config.pin_pclk = CAM_PIN_PCLK;
  config.pin_vsync = CAM_PIN_VSYNC;
  config.pin_href = CAM_PIN_HREF;

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  config.pin_sccb_sda = CAM_PIN_SIOD;
  config.pin_sccb_scl = CAM_PIN_SIOC;
#else
  config.pin_sscb_sda = CAM_PIN_SIOD;
  config.pin_sscb_scl = CAM_PIN_SIOC;
#endif

  config.pin_pwdn = CAM_PIN_PWDN;
  config.pin_reset = CAM_PIN_RESET;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_VGA;
  config.jpeg_quality = 12;
  config.fb_count = 1;

  return esp_camera_init(&config) == ESP_OK;
}

}  // namespace

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(500);
  Serial.println();
  Serial.println("HerbiBioS boot");

  ESP32PWM::allocateTimer(1);
  loadServoPositions();

  // Start Wi-Fi before camera initialization so a camera failure does not
  // prevent the access point from starting.
  const bool apReady = startAccessPoint();

  cameraReady = startCamera();
  Serial.println(cameraReady ? "CAMERA_READY" : "CAMERA_ERROR");

  server.on("/", HTTP_GET, []() {
    sendText(200, "text/plain", "HerbiBioS ready. GET /health and /capture");
  });
  server.on("/health", HTTP_GET, handleHealth);
  server.on("/capture", HTTP_GET, handleCapture);
  server.onNotFound([]() {
    sendText(404, "text/plain", "Not found");
  });
  server.begin();

  if (apReady) {
    Serial.print("AP_IP ");
    Serial.println(WiFi.softAPIP());
  }

  Serial.println("READY");
}

void loop() {
  server.handleClient();
  readSerialCommands();

  if (activeServo >= 0 &&
      static_cast<int32_t>(millis() - servoCloseAt) >= 0) {
    const int index = activeServo;
    servos[index].write(closedAngles[index]);

    Serial.println(activeDoneMessage);
    activeDoneMessage[0] = '\0';
    activeServo = -1;
  }
}