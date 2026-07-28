{{/*
Common labels applied to all resources.
*/}}
{{- define "drone-ai.labels" -}}
app.kubernetes.io/managed-by: Helm
app.kubernetes.io/part-of: drone-ai
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/*
Full image reference: registry/image:tag
*/}}
{{- define "drone-ai.image" -}}
{{- $registry := .root.Values.global.imageRegistry -}}
{{- if $registry -}}
{{ $registry }}{{ .image }}:{{ .tag | default "latest" }}
{{- else -}}
{{ .image }}:{{ .tag | default "latest" }}
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
  value: "my-kafka.{{ .Values.global.namespace }}.svc.cluster.local:9092"
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
*/}}
{{- define "colmap.workDrivesJson" -}}
[{{- range $i, $d := .Values.colmapWorker.workVolume.drives }}{{- if $i }},{{ end }}{"name":"{{ $d.name }}","label":"{{ $d.label }}","mount":"/work/{{ $d.name }}"}{{- end }}]
{{- end }}
