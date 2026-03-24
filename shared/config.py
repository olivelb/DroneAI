import os

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "my-kafka.kafka.svc.cluster.local:9092")

TOPIC_MISSION = "vols-bruts"
TOPIC_ORTHO = "images-ortho"
TOPIC_STATUS = "pipeline-status"
TOPIC_CONTROL = "pipeline-control"
TOPIC_IMAGE_TILES = "image-tiles"
TOPIC_TILE_DETECTIONS = "tile-detections"

DEFAULT_WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/mnt/j/workspace")

SERVICE_ORDER = ["COLMAP", "TILER", "IA"]