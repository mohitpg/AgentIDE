{{/* Labels on every object in the chart. Objects of one service add its
     selectorLabels too. */}}
{{- define "agentide-data.labels" -}}
app.kubernetes.io/part-of: agentide
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}

{{/* Selector labels for one service. Call with (dict "root" $ "name" "redis"). */}}
{{- define "agentide-data.selectorLabels" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/name: {{ .name }}
{{- end }}

{{/* One ReadWriteOnce volumeClaimTemplate named "data". Call with a storage
     block ({size, storageClass}). */}}
{{- define "agentide-data.volumeClaim" -}}
- metadata:
    name: data
  spec:
    accessModes: ["ReadWriteOnce"]
    {{- with .storageClass }}
    storageClassName: {{ . }}
    {{- end }}
    resources:
      requests:
        storage: {{ .size }}
{{- end }}
