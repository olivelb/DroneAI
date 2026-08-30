{{/*
Common labels applied to all resources.
*/}}
{{- define "drone-ai.labels" -}}
app.kubernetes.io/managed-by: Helm
app.kubernetes.io/part-of: drone-ai
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/*
Full application image reference: registry/image:tag or registry/image@sha256.
Protected environments require an OCI digest, regardless of the optional
local immutable-tag guard. Local development retains the latest fallback.
A digest embedded in the image value ignores tag. imageRegistry is prepended
in both cases; leave it empty when supplying full registry references.
*/}}
{{- define "drone-ai.image" -}}
{{- $registry := .root.Values.global.imageRegistry -}}
{{- $tag := .tag | default "latest" -}}
{{- $hasDigestMarker := contains "@sha256:" .image -}}
{{- $hasDigest := regexMatch "@sha256:[0-9a-f]{64}$" .image -}}
{{- $hasGitSha := regexMatch "^[0-9a-f]{7,40}$" $tag -}}
{{- if and $hasDigestMarker (not $hasDigest) -}}
{{- fail (printf "application image %q contains a malformed OCI SHA-256 digest" .image) -}}
{{- end -}}
{{- $protected := has .root.Values.dashboardApi.environment (list "staging" "production") -}}
{{- if and $protected (not $hasDigest) -}}
{{- fail (printf "application image %q must use an OCI digest in staging and production" .image) -}}
{{- end -}}
{{- if and .root.Values.global.requireImmutableImages (not $hasDigest) (not $hasGitSha) -}}
{{- fail (printf "application image %q must use a 7-40 character lower-case Git SHA tag or @sha256 digest" .image) -}}
{{- end -}}
{{- $repository := printf "%s%s" $registry .image -}}
{{- if $hasDigest -}}
{{ $repository }}
{{- else -}}
{{ $repository }}:{{ $tag }}
{{- end -}}
{{- end }}

{{/*
Namespace helper — always use global.namespace
*/}}
{{- define "drone-ai.namespace" -}}
{{ .Values.global.namespace }}
{{- end }}

{{/*
Storage Secret name: generated for local development, externally managed in
production.
*/}}
{{- define "drone-ai.storageSecretName" -}}
{{- default "drone-ai-storage" .Values.storage.existingSecret -}}
{{- end }}

{{/*
Common storage and database environment variables. Callers choose the database
Secret key so the HTTP API can use a non-owner RLS role while migrations and
workers retain their operator connection.
*/}}
{{- define "drone-ai.commonEnv" -}}
{{- $root := .root -}}
- name: DRONEAI_ENV
  value: {{ $root.Values.dashboardApi.environment | quote }}
- name: KAFKA_BROKER
  value: {{ default (printf "my-kafka.%s.svc.cluster.local:9092" $root.Values.global.namespace) $root.Values.kafka.broker | quote }}
- name: KAFKA_SECURITY_PROTOCOL
  value: {{ $root.Values.kafka.security.protocol | quote }}
{{- if hasPrefix "SASL_" $root.Values.kafka.security.protocol }}
- name: KAFKA_SASL_MECHANISM
  value: {{ $root.Values.kafka.security.saslMechanism | quote }}
- name: KAFKA_SASL_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.kafka.security.existingSecret }}
      key: {{ $root.Values.kafka.security.usernameSecretKey }}
- name: KAFKA_SASL_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.kafka.security.existingSecret }}
      key: {{ $root.Values.kafka.security.passwordSecretKey }}
{{- end }}
{{- if $root.Values.kafka.security.tls.existingSecret }}
- name: KAFKA_SSL_CA_LOCATION
  value: /var/run/secrets/droneai/kafka/{{ $root.Values.kafka.security.tls.caSecretKey }}
{{- if $root.Values.kafka.security.tls.certificateSecretKey }}
- name: KAFKA_SSL_CERTIFICATE_LOCATION
  value: /var/run/secrets/droneai/kafka/{{ $root.Values.kafka.security.tls.certificateSecretKey }}
{{- end }}
{{- if $root.Values.kafka.security.tls.privateKeySecretKey }}
- name: KAFKA_SSL_KEY_LOCATION
  value: /var/run/secrets/droneai/kafka/{{ $root.Values.kafka.security.tls.privateKeySecretKey }}
{{- end }}
{{- end }}
- name: CANCELLATION_POLL_SECONDS
  value: {{ $root.Values.kafka.cancellationPollSeconds | quote }}
- name: S3_ENDPOINT
  value: {{ $root.Values.storage.s3Endpoint | quote }}
- name: S3_BUCKET
  value: {{ $root.Values.storage.s3Bucket | quote }}
- name: S3_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "drone-ai.storageSecretName" $root }}
      key: {{ $root.Values.storage.accessKeySecretKey }}
- name: S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "drone-ai.storageSecretName" $root }}
      key: {{ $root.Values.storage.secretKeySecretKey }}
- name: S3_REGION
  value: {{ $root.Values.storage.s3Region | quote }}
- name: S3_PUBLIC_ENDPOINT
  value: {{ $root.Values.storage.s3PublicEndpoint | quote }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "drone-ai.storageSecretName" $root }}
      key: {{ .databaseUrlSecretKey }}
{{- end }}

{{/*
JSON array of available work drives for the colmap worker.
Each entry: {"name": "...", "label": "...", "mount": "/work/..."}
Only drives backed by an actual pod volume are advertised.
*/}}
{{- define "colmap.workDrivesJson" -}}
[
{{- $first := true -}}
{{- range $d := .Values.colmapWorker.workVolume.drives -}}
  {{- $configured := or (eq (default "" $d.type) "emptyDir") (not (empty $d.hostPath)) (not (empty $d.existingClaim)) -}}
  {{- if $configured -}}
    {{- if not $first }},{{ end -}}
    {{- dict "name" $d.name "label" $d.label "mount" (printf "/work/%s" $d.name) | toJson -}}
    {{- $first = false -}}
  {{- end -}}
{{- end -}}
]
{{- end }}

{{/*
Fail the Helm render if the configured default is not one of the volumes that
will actually be mounted. This prevents a healthy dashboard from advertising
an unusable selection.
*/}}
{{- define "colmap.assertWorkDriveConfig" -}}
{{- $defaultFound := false -}}
{{- $names := dict -}}
{{- range $d := .Values.colmapWorker.workVolume.drives -}}
  {{- $sourceCount := 0 -}}
  {{- if eq (default "" $d.type) "emptyDir" }}{{- $sourceCount = add1 $sourceCount -}}{{- end -}}
  {{- if not (empty $d.hostPath) }}{{- $sourceCount = add1 $sourceCount -}}{{- end -}}
  {{- if not (empty $d.existingClaim) }}{{- $sourceCount = add1 $sourceCount -}}{{- end -}}
  {{- if ne $sourceCount 1 -}}
    {{- fail (printf "work drive %q must define exactly one of type=emptyDir, hostPath, or existingClaim" $d.name) -}}
  {{- end -}}
  {{- if hasKey $names $d.name -}}
    {{- fail (printf "work drive name %q is duplicated" $d.name) -}}
  {{- end -}}
  {{- $_ := set $names $d.name true -}}
  {{- $configured := or (eq (default "" $d.type) "emptyDir") (not (empty $d.hostPath)) (not (empty $d.existingClaim)) -}}
  {{- if and $configured (eq $d.name $.Values.colmapWorker.workVolume.default) -}}
    {{- $defaultFound = true -}}
  {{- end -}}
{{- end -}}
{{- if not $defaultFound -}}
  {{- fail (printf "colmapWorker.workVolume.default %q is not a configured work drive" .Values.colmapWorker.workVolume.default) -}}
{{- end -}}
{{- end }}
