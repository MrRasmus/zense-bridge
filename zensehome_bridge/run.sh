#!/usr/bin/with-contenv bashio
set -e

export PYTHONUNBUFFERED=1

ZENSE_IP="$(bashio::config 'zense_ip')"
ZENSE_PORT="$(bashio::config 'zense_port')"
ZENSE_CODE="$(bashio::config 'zense_code')"

MQTT_HOST="$(bashio::config 'mqtt_host')"
MQTT_PORT="$(bashio::config 'mqtt_port')"
MQTT_USER="$(bashio::config 'mqtt_user')"
MQTT_PASS="$(bashio::config 'mqtt_pass')"

# Auto-use Supervisor MQTT service if mqtt_host is empty
if bashio::var.is_empty "${MQTT_HOST}"; then
  if bashio::services.available 'mqtt'; then
    MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    MQTT_USER="$(bashio::services 'mqtt' 'username')"
    MQTT_PASS="$(bashio::services 'mqtt' 'password')"
  fi
fi

export ZENSE_IP ZENSE_PORT ZENSE_CODE
export MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASS

export STATE_POLL_SEC="$(bashio::config 'state_poll_sec')"
export DEBOUNCE_MS="$(bashio::config 'debounce_ms')"
export CMD_GAP_SEC="$(bashio::config 'cmd_gap_sec')"
export LEVEL_ON_WINDOW_SEC="$(bashio::config 'level_on_window_sec')"
export DEBUG_MQTT="$(bashio::config 'debug_mqtt')"

exec python3 -u /app/zense_mqtt_bridge.py
