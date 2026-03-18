#!/bin/bash
KAFKA_IP=$(sudo docker inspect kafka-local -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
echo "Using Kafka at $KAFKA_IP"

# Nettoyage
sudo docker rm -f colmap-worker ia-worker processing-worker || true

# On monte tout / pour permettre l'accès à n'importe quel disque (SSD externe, etc)
# Le worker utilisera les chemins absolus fournis par l'UI.

# Run App 1 (COLMAP)
sudo docker run -d --name colmap-worker \
  --gpus all \
  -v /:/host \
  --add-host my-kafka.kafka.svc.cluster.local:$KAFKA_IP \
  --workdir /host/home/olivier/app1-colmap \
  -e KAFKA_BROKER=my-kafka.kafka.svc.cluster.local:9092 \
  -e QT_QPA_PLATFORM=offscreen \
  drone-pipeline:latest \
  python3 /host/home/olivier/app1-colmap/main.py

# Run App 2 (IA)
sudo docker run -d --name ia-worker \
  --gpus all \
  -v /:/host \
  --add-host my-kafka.kafka.svc.cluster.local:$KAFKA_IP \
  --workdir /host/home/olivier/app2-ia \
  -e KAFKA_BROKER=my-kafka.kafka.svc.cluster.local:9092 \
  drone-pipeline:latest \
  python3 /host/home/olivier/app2-ia/main.py

# Run App 3 (Processing)
sudo docker run -d --name processing-worker \
  -v /:/host \
  --add-host my-kafka.kafka.svc.cluster.local:$KAFKA_IP \
  --workdir /host/home/olivier/app3-processing \
  -e KAFKA_BROKER=my-kafka.kafka.svc.cluster.local:9092 \
  drone-pipeline:latest \
  python3 /host/home/olivier/app3-processing/main.py

