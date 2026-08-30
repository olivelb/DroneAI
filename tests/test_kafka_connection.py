import pytest

from shared.kafka_connection import KafkaConnectionSettings


def test_plaintext_defaults_remain_available_only_for_local_development():
    settings = KafkaConnectionSettings.from_environment({})

    assert settings.client_config() == {
        "bootstrap.servers": "my-kafka.drone-ai.svc.cluster.local:9092",
        "security.protocol": "PLAINTEXT",
    }


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("protocol", ["PLAINTEXT", "SASL_PLAINTEXT"])
def test_protected_environments_refuse_unencrypted_kafka(environment, protocol):
    values = {
        "DRONEAI_ENV": environment,
        "KAFKA_BROKER": "kafka.example:9093",
        "KAFKA_SECURITY_PROTOCOL": protocol,
        "KAFKA_SASL_MECHANISM": "SCRAM-SHA-512",
        "KAFKA_SASL_USERNAME": "dashboard",
        "KAFKA_SASL_PASSWORD": "secret",
    }

    with pytest.raises(RuntimeError, match="must use SSL or SASL_SSL"):
        KafkaConnectionSettings.from_environment(values)


def test_sasl_ssl_client_config_contains_authentication_and_tls():
    settings = KafkaConnectionSettings.from_environment(
        {
            "DRONEAI_ENV": "production",
            "KAFKA_BROKER": "kafka.example:9093",
            "KAFKA_SECURITY_PROTOCOL": "SASL_SSL",
            "KAFKA_SASL_MECHANISM": "SCRAM-SHA-512",
            "KAFKA_SASL_USERNAME": "dashboard",
            "KAFKA_SASL_PASSWORD": "secret",
            "KAFKA_SSL_CA_LOCATION": "/var/run/kafka/ca.crt",
        }
    )

    assert settings.client_config() == {
        "bootstrap.servers": "kafka.example:9093",
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-512",
        "sasl.username": "dashboard",
        "sasl.password": "secret",
        "ssl.ca.location": "/var/run/kafka/ca.crt",
    }
    assert "secret" not in repr(settings)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"KAFKA_SASL_PASSWORD": ""}, "username and password"),
        ({"KAFKA_SASL_MECHANISM": "OAUTHBEARER"}, "not supported"),
    ],
)
def test_sasl_configuration_fails_closed(override, message):
    values = {
        "KAFKA_BROKER": "kafka.example:9093",
        "KAFKA_SECURITY_PROTOCOL": "SASL_SSL",
        "KAFKA_SASL_MECHANISM": "SCRAM-SHA-512",
        "KAFKA_SASL_USERNAME": "dashboard",
        "KAFKA_SASL_PASSWORD": "secret",
    }
    values.update(override)

    with pytest.raises(RuntimeError, match=message):
        KafkaConnectionSettings.from_environment(values)


def test_tls_client_certificate_and_key_must_be_paired():
    with pytest.raises(RuntimeError, match="configured together"):
        KafkaConnectionSettings.from_environment(
            {
                "KAFKA_BROKER": "kafka.example:9093",
                "KAFKA_SECURITY_PROTOCOL": "SSL",
                "KAFKA_SSL_CERTIFICATE_LOCATION": "/certs/client.crt",
            }
        )
