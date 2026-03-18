import os, json
from confluent_kafka import Producer

KAFKA_BROKER = "my-kafka.kafka.svc.cluster.local:9092"
producer = Producer({'bootstrap.servers': KAFKA_BROKER})

def acked(err, msg):
    if err is not None:
        print("Failed to deliver message: %s: %s" % (str(msg), str(err)))
    else:
        print("Message produced: %s" % (str(msg)))

msg = {"vol_id": "vol_test_pipeline", "input_dir": "/home/olivier/workspace/test_dataset", "workspace_dir": "/home/olivier/workspace"}
producer.produce("vols-bruts", key="test", value=json.dumps(msg), callback=acked)
producer.flush(10)
