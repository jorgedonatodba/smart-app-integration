import os, json
import psycopg2
import paho.mqtt.client as mqtt
from prometheus_client import Counter, Gauge, start_http_server

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "uns/#")

METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))

msg_count = Counter("mqtt_messages_total", "MQTT messages processed", ["topic"])
err_count = Counter("connector_errors_total", "Connector errors")
last_ts = Gauge("connector_last_message_unix", "Last message timestamp (unix)")

pg = psycopg2.connect(
  host=os.getenv("PGHOST","localhost"),
  port=os.getenv("PGPORT","5432"),
  dbname=os.getenv("PGDATABASE","historian"),
  user=os.getenv("PGUSER","postgres"),
  password=os.getenv("PGPASSWORD","postgres"),
)
pg.autocommit = True

def on_message(client, userdata, msg):
  try:
    payload = json.loads(msg.payload.decode("utf-8"))
    ts = payload.get("ts")
    val = payload.get("value")
    with pg.cursor() as cur:
      cur.execute(
        "INSERT INTO measurements(ts, topic, value, payload) VALUES (%s, %s, %s, %s::jsonb)",
        (ts, msg.topic, val, json.dumps(payload)),
      )
    msg_count.labels(topic=msg.topic).inc()
    # best-effort: record "now"
    import time
    last_ts.set(time.time())
  except Exception:
    err_count.inc()

def main():
  start_http_server(METRICS_PORT)
  c = mqtt.Client()
  c.on_message = on_message
  c.connect(MQTT_HOST, MQTT_PORT, 60)
  c.subscribe(MQTT_TOPIC, qos=1)
  c.loop_forever()

if __name__ == "__main__":
  main()
