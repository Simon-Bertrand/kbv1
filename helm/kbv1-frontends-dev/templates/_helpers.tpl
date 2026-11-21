{{/*
Expand the name of the chart.
*/}}
{{- define "kbv1-frontends.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "kbv1-frontends.fullname" -}}
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
{{- define "kbv1-frontends.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "kbv1-frontends.labels" -}}
helm.sh/chart: {{ include "kbv1-frontends.chart" . }}
{{ include "kbv1-frontends.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
environment: {{ .Values.global.environment | default "dev" | quote }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "kbv1-frontends.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kbv1-frontends.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
