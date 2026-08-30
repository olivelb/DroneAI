"""Validated Kafka connection settings shared by every application client."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


_PROTECTED_ENVIRONMENTS = {"staging", "production"}
_ENCRYPTED_PROTOCOLS = {"SSL", "SASL_SSL"}
_SUPPORTED_PROTOCOLS = {"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"}
_SUPPORTED_SASL_MECHANISMS = {"PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"}


@dataclass(frozen=True)
class KafkaConnectionSettings:
    bootstrap_servers: str
    security_protocol: str = "PLAINTEXT"
    sasl_mechanism: str = ""
    sasl_username: str = ""
    sasl_password: str = field(default="", repr=False)
    ssl_ca_location: str = ""
    ssl_certificate_location: str = ""
    ssl_key_location: str = field(default="", repr=False)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> KafkaConnectionSettings:
        values = os.environ if environ is None else environ
        settings = cls(
            bootstrap_servers=values.get(
                "KAFKA_BROKER", "my-kafka.drone-ai.svc.cluster.local:9092"
            ).strip(),
            security_protocol=values.get(
                "KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"
            ).strip().upper(),
            sasl_mechanism=values.get("KAFKA_SASL_MECHANISM", "").strip().upper(),
            sasl_username=values.get("KAFKA_SASL_USERNAME", "").strip(),
            sasl_password=values.get("KAFKA_SASL_PASSWORD", ""),
            ssl_ca_location=values.get("KAFKA_SSL_CA_LOCATION", "").strip(),
            ssl_certificate_location=values.get(
                "KAFKA_SSL_CERTIFICATE_LOCATION", ""
            ).strip(),
            ssl_key_location=values.get("KAFKA_SSL_KEY_LOCATION", "").strip(),
        )
        settings.validate(values.get("DRONEAI_ENV", "development"))
        return settings

    def validate(self, environment: str) -> None:
        if not self.bootstrap_servers:
            raise RuntimeError("KAFKA_BROKER must not be empty")
        if self.security_protocol not in _SUPPORTED_PROTOCOLS:
            raise RuntimeError("KAFKA_SECURITY_PROTOCOL is not supported")
        if environment.strip().lower() in _PROTECTED_ENVIRONMENTS:
            if self.security_protocol not in _ENCRYPTED_PROTOCOLS:
                raise RuntimeError(
                    "Kafka transport must use SSL or SASL_SSL in staging and production"
                )
        if self.security_protocol.startswith("SASL"):
            if self.sasl_mechanism not in _SUPPORTED_SASL_MECHANISMS:
                raise RuntimeError("KAFKA_SASL_MECHANISM is not supported")
            if not self.sasl_username or not self.sasl_password:
                raise RuntimeError(
                    "Kafka SASL username and password are required for SASL protocols"
                )
        elif self.sasl_mechanism or self.sasl_username or self.sasl_password:
            raise RuntimeError("Kafka SASL settings require a SASL security protocol")
        if bool(self.ssl_certificate_location) != bool(self.ssl_key_location):
            raise RuntimeError(
                "Kafka TLS client certificate and private key must be configured together"
            )

    def client_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {
            "bootstrap.servers": self.bootstrap_servers,
            "security.protocol": self.security_protocol,
        }
        if self.security_protocol.startswith("SASL"):
            config.update(
                {
                    "sasl.mechanism": self.sasl_mechanism,
                    "sasl.username": self.sasl_username,
                    "sasl.password": self.sasl_password,
                }
            )
        if self.security_protocol in _ENCRYPTED_PROTOCOLS:
            if self.ssl_ca_location:
                config["ssl.ca.location"] = self.ssl_ca_location
            if self.ssl_certificate_location:
                config["ssl.certificate.location"] = self.ssl_certificate_location
                config["ssl.key.location"] = self.ssl_key_location
        return config


def kafka_connection_settings() -> KafkaConnectionSettings:
    """Read and validate Kafka settings at client construction time."""

    return KafkaConnectionSettings.from_environment()
