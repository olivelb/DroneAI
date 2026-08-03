import os

DRONEAI_ENV = os.getenv("DRONEAI_ENV", "development").strip().lower()

# ---------------------------------------------------------------------------
# Kafka
# ---------------------------------------------------------------------------

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "my-kafka.drone-ai.svc.cluster.local:9092")

TOPIC_MISSION = "vols-bruts"
TOPIC_ORTHO = "images-ortho"
TOPIC_STATUS = "pipeline-status"
TOPIC_CONTROL = "pipeline-control"
TOPIC_IMAGE_TILES = "image-tiles"
TOPIC_TILE_DETECTIONS = "tile-detections"
TOPIC_DEAD_LETTER = os.getenv("TOPIC_DEAD_LETTER", "pipeline-dead-letter")

# ---------------------------------------------------------------------------
# S3 / Object Storage (MinIO locally, managed S3 in cloud)
# ---------------------------------------------------------------------------

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio.drone-ai.svc:9000")
S3_BUCKET = os.getenv("S3_BUCKET", "drone-ai")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin")
S3_REGION = os.getenv("S3_REGION", "us-east-1")

# ---------------------------------------------------------------------------
# PostgreSQL + PostGIS
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://droneai:droneai-local@postgres.drone-ai.svc:5432/droneai",
)

# ---------------------------------------------------------------------------
# Worker workspace
# ---------------------------------------------------------------------------

DEFAULT_WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/work/system")

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

SERVICE_ORDER = ["COLMAP", "TILER", "IA"]
