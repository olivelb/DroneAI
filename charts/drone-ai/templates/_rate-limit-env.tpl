{{/* API consumption and background expiry must use identical quotas. */}}
{{- define "drone-ai.rateLimitEnv" -}}
- name: DRONEAI_IDENTITY_RATE_LIMIT_BACKEND
  value: {{ .Values.dashboardApi.auth.rateLimitBackend | quote }}
- name: DRONEAI_IDENTITY_PEER_RATE_LIMIT_PER_MINUTE
  value: {{ .Values.dashboardApi.auth.peerRateLimitPerMinute | quote }}
- name: DRONEAI_IDENTITY_PEER_RATE_LIMIT_BURST
  value: {{ .Values.dashboardApi.auth.peerRateLimitBurst | quote }}
- name: DRONEAI_IDENTITY_CREDENTIAL_RATE_LIMIT_PER_MINUTE
  value: {{ .Values.dashboardApi.auth.credentialRateLimitPerMinute | quote }}
- name: DRONEAI_IDENTITY_CREDENTIAL_RATE_LIMIT_BURST
  value: {{ .Values.dashboardApi.auth.credentialRateLimitBurst | quote }}
- name: DRONEAI_IDENTITY_RATE_LIMIT_MAX_CLIENTS
  value: {{ .Values.dashboardApi.auth.rateLimitMaxClients | quote }}
- name: DRONEAI_TILE_RATE_LIMIT_PER_MINUTE
  value: {{ .Values.dashboardApi.tiles.rateLimitPerMinute | quote }}
- name: DRONEAI_TILE_RATE_LIMIT_BACKEND
  value: {{ .Values.dashboardApi.tiles.rateLimitBackend | quote }}
- name: DRONEAI_TILE_RATE_LIMIT_BURST
  value: {{ .Values.dashboardApi.tiles.rateLimitBurst | quote }}
- name: DRONEAI_TILE_RATE_LIMIT_MAX_CLIENTS
  value: {{ .Values.dashboardApi.tiles.rateLimitMaxClients | quote }}
{{- end -}}
