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
Production overlays may reject the mutable latest fallback while local
development retains it. A digest embedded in the image value is already an
immutable full repository reference and therefore ignores tag.
*/}}
{{- define "drone-ai.image" -}}
{{- $registry := .root.Values.global.imageRegistry -}}
{{- $tag := .tag | default "latest" -}}
{{- $hasDigest := contains "@sha256:" .image -}}
{{- if and .root.Values.global.requireImmutableImages (not $hasDigest) (eq $tag "latest") -}}
{{- fail (printf "production application image %q must use an immutable tag or @sha256 digest" .image) -}}
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
Common environment variables injected into all worker pods
*/}}
{{- define "drone-ai.commonEnv" -}}
- name: KAFKA_BROKER
  value: {{ default (printf "my-kafka.%s.svc.cluster.local:9092" .Values.global.namespace) .Values.kafka.broker | quote }}
- name: INBOX_LEASE_SECONDS
  value: {{ .Values.kafka.workerInbox.leaseSeconds | quote }}
- name: INBOX_BUSY_RETRY_SECONDS
  value: {{ .Values.kafka.workerInbox.busyRetrySeconds | quote }}
- name: CANCELLATION_POLL_SECONDS
  value: {{ .Values.kafka.cancellationPollSeconds | quote }}
- name: S3_ENDPOINT
  value: {{ .Values.storage.s3Endpoint | quote }}
- name: S3_BUCKET
  value: {{ .Values.storage.s3Bucket | quote }}
- name: S3_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "drone-ai.storageSecretName" . }}
      key: {{ .Values.storage.accessKeySecretKey }}
- name: S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "drone-ai.storageSecretName" . }}
      key: {{ .Values.storage.secretKeySecretKey }}
- name: S3_REGION
  value: {{ .Values.storage.s3Region | quote }}
- name: S3_PUBLIC_ENDPOINT
  value: {{ .Values.storage.s3PublicEndpoint | quote }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "drone-ai.storageSecretName" . }}
      key: {{ .Values.storage.databaseUrlSecretKey }}
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
