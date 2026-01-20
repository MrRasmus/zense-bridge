ARG BUILD_FROM=ghcr.io/hassio-addons/base:15.0.0
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip
RUN pip3 install --no-cache-dir paho-mqtt

COPY run.sh /run.sh
COPY zense_mqtt_bridge.py /app/zense_mqtt_bridge.py

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
