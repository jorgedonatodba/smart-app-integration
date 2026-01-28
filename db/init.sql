CREATE TABLE IF NOT EXISTS measurements (
  ts timestamptz NOT NULL,
  topic text NOT NULL,
  value double precision NULL,
  payload jsonb NOT NULL
);

SELECT create_hypertable('measurements', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_measurements_topic_ts ON measurements(topic, ts DESC);
