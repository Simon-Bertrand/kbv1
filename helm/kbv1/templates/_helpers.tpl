{{/*
Expand the name of the chart.
*/}}
{{- define "kbv1.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "kbv1.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "kbv1.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "kbv1.labels" -}}
helm.sh/chart: {{ include "kbv1.chart" . }}
{{ include "kbv1.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
environment: {{ .Values.global.environment | default "dev" | quote }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "kbv1.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kbv1.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "kbv1.image" -}}
{{- $repo := .image.repository -}}
{{- with .root.Values.global.imageRegistry -}}
{{- $repo = printf "%s/%s" (trimSuffix "/" .) (last (splitList "/" $repo)) -}}
{{- end -}}
{{- printf "%s:%s" $repo (default .image.tag .root.Values.global.imageTag) -}}
{{- end -}}

{{- define "kbv1.secretName" -}}
{{- default (printf "%s-secrets" (include "kbv1.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}
