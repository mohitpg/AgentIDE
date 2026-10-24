{{/* The environment name (dev / prod). */}}
{{- define "agentide.env" -}}
{{- required "env is required: dev or prod (use -f values-<env>.yaml)" .Values.env -}}
{{- end }}

{{/* Labels on every object in the chart. Objects of one component add its
     selectorLabels too. */}}
{{- define "agentide.labels" -}}
app.kubernetes.io/part-of: agentide
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
agentide.dev/env: {{ include "agentide.env" . }}
{{- end }}

{{/* Selector labels for one component. Call with (dict "root" $ "name" "api"). */}}
{{- define "agentide.selectorLabels" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/name: {{ .name }}
{{- end }}

{{/* A component's image. Call with (dict "root" $ "name" "api"). */}}
{{- define "agentide.image" -}}
{{- $v := .root.Values -}}
{{- $registry := $v.image.registry -}}
{{- if not $registry -}}
{{- $project := required "gcp.project is required (or set image.registry)" $v.gcp.project -}}
{{- $registry = printf "%s-docker.pkg.dev/%s/agentide" $v.gcp.region $project -}}
{{- end -}}
{{- printf "%s/%s:%s" $registry .name (required "image.tag is required, e.g. --set image.tag=sha-abc1234" $v.image.tag) -}}
{{- end }}

{{/* Pod annotations: restart on ConfigMap or app-secret changes. */}}
{{- define "agentide.podAnnotations" -}}
checksum/config: {{ include (print .Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- with .Values.secretsChecksum }}
agentide.dev/secrets-checksum: {{ . | quote }}
{{- end }}
{{- end }}

{{/* The images run as 10001:10001 (infra/docker/python.Dockerfile). No fsGroup:
     the only volume is the GCS FUSE mount, which takes uid/gid as options. */}}
{{- define "agentide.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "agentide.containerSecurityContext" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Postgres URLs for the app user, from the CNPG <cluster>-app secret.
     Kubernetes expands $(VAR) from vars defined earlier in the list. The
     operator's generated passwords are alphanumeric, so they need no
     URL-encoding. */}}
{{- define "agentide.databaseEnv" -}}
{{- $pg := .Values.postgres -}}
{{- $secret := printf "%s-app" $pg.cluster -}}
{{- $target := printf "$(PG_USER):$(PG_PASSWORD)@%s-rw:5432/%s" $pg.cluster $pg.database -}}
- name: PG_USER
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: username
- name: PG_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: password
- name: DATABASE_URL
  value: "postgresql+asyncpg://{{ $target }}"
- name: DATABASE_URL_SYNC
  value: "postgresql+psycopg2://{{ $target }}"
- name: DATABASE_URL_PSYCOPG
  value: "postgresql://{{ $target }}"
{{- end }}

{{/* RabbitMQ URL from the data chart's password secret (hex, URL-safe). */}}
{{- define "agentide.rabbitmqEnv" -}}
{{- $mq := .Values.rabbitmq -}}
- name: RABBITMQ_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $mq.passwordSecret.name }}
      key: {{ $mq.passwordSecret.key }}
- name: RABBITMQ_URL
  value: "amqp://{{ $mq.user }}:$(RABBITMQ_PASSWORD)@{{ $mq.host }}:5672/"
{{- end }}

{{/* Env vars from single keys of the app secret. Optional, so a key the
     workflow leaves out (e.g. an unused provider's API key) doesn't block the
     pod. Call with (dict "root" $ "keys" (list "A2A_AUTH_TOKEN")). */}}
{{- define "agentide.secretKeyEnv" -}}
{{- range .keys }}
- name: {{ . }}
  valueFrom:
    secretKeyRef:
      name: {{ $.root.Values.secretName }}
      key: {{ . }}
      optional: true
{{- end }}
{{- end }}

{{- define "agentide.uploadsBucket" -}}
{{- if .Values.uploads.bucket -}}
{{- .Values.uploads.bucket -}}
{{- else -}}
{{- printf "%s-agentide-uploads-%s" (required "gcp.project is required (or set uploads.bucket)" .Values.gcp.project) (include "agentide.env" .) -}}
{{- end -}}
{{- end }}

{{/* The /data volume: the env's uploads bucket over GCS FUSE. Pods using it
     also need the gke-gcsfuse/volumes annotation. */}}
{{- define "agentide.dataVolume" -}}
- name: data
{{- if .Values.uploads.enabled }}
  csi:
    driver: gcsfuse.csi.storage.gke.io
    volumeAttributes:
      bucketName: {{ include "agentide.uploadsBucket" . }}
      mountOptions: {{ .Values.uploads.mountOptions | quote }}
{{- else }}
  # Pod-local scratch: chat images work, but the worker can't read the api's
  # RAG uploads.
  emptyDir: {}
{{- end }}
{{- end }}
