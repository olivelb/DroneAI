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
      name: drone-ai-storage
      key: s3-access-key
- name: S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: drone-ai-storage
      key: s3-secret-key
- name: S3_REGION
  value: {{ .Values.storage.s3Region | quote }}
- name: S3_PUBLIC_ENDPOINT
  value: {{ .Values.storage.s3PublicEndpoint | quote }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: drone-ai-storage
      key: database-url
{{- end }}
