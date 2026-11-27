{{/*
Choisit le nom par défaut ou nameOverride et le limite à 63 caractères.
*/}}
{{- define "kbv1.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Construit le nom de base des ressources à partir de la release, ou applique fullnameOverride.
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
Construit une étiquette Helm valide avec le nom et la version du chart.
*/}}
{{- define "kbv1.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Ajoute les étiquettes de gestion Helm, de version et d’environnement.
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
Conserve des sélecteurs stables fondés sur le nom du chart et l’identité de la release.
*/}}
{{- define "kbv1.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kbv1.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- /* Compose la référence de l’image à partir du registre requis, du dépôt et du tag global. */ -}}
{{- define "kbv1.image" -}}
{{- $registry := required "Renseigner global.imageRegistry avec le REGISTRY_PATH GitLab" .root.Values.global.imageRegistry -}}
{{- printf "%s/%s:%s" (trimSuffix "/" $registry) .image.repository .root.Values.global.imageTag -}}
{{- end -}}

{{- /* Exige le nom du Secret runtime existant qui fournit les identifiants. */ -}}
{{- define "kbv1.secretName" -}}
{{- required "Renseigner secrets.existingSecret" .Values.secrets.existingSecret -}}
{{- end -}}

{{- /* Combine le sous-domaine de la route et le domaine commun pour former son hôte public. */ -}}
{{- define "kbv1.host" -}}
{{- printf "%s.%s" (index .root.Values.ingress.subdomains .name) .root.Values.domain_name -}}
{{- end -}}
