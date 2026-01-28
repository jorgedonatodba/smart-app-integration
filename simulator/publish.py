import os, json, time, random, datetime
import paho.mqtt.client as mqtt

host = os.getenv("MQTT_HOST", "localhost")
port = int(os.getenv("MQTT_PORT", "1883"))

topics = [
  "uns/man/munich/line1/cell2/press01/temperature",
  "uns/man/munich/line1/cell2/press01/vibration",
  "uns/man/munich/line1/cell2/press01/state",
]

c = mqtt.Client()
c.connect(host, port, 60)
c.loop_start()

while True:
  for t in topics:
    payload = {
      "ts": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
      "value": round(random.uniform(10, 90), 2),
      "quality": "good",
    }
    if t.endswith("/state"):
      payload["value"] = random.choice([0, 1, 2, 3])
    c.publish(t, json.dumps(payload), qos=1)
  time.sleep(1)
