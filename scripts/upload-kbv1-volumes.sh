#!/usr/bin/env bash
# Copie de secours depuis la machine locale vers les deux PVC du chart Helm.
# pipefail propage une erreur de tar ou de kubectl et arrête le transfert.
set -euo pipefail

# Les deux paramètres facultatifs sont le namespace Kubernetes et le nom de la release Helm.
if (( $# > 2 )); then
    printf 'Usage : bash %s [namespace] [release]\n' "$0" >&2
    exit 2
fi

namespace=${1:-kbv1}
release=${2:-kbv1}
# Résout la racine du dépôt à partir du script pour trouver data et models depuis tout répertoire.
project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# Vérifie les deux sources locales avant de créer un pod.
for directory in data models; do
    if [[ ! -d "$project_dir/$directory" ]]; then
        printf 'Dossier local manquant : %s/%s\n' "$project_dir" "$directory" >&2
        exit 1
    fi
done

# Utilise le contexte kubectl actif et sélectionne PdfParse par les étiquettes de la release.
kube=(kubectl --namespace "$namespace")
deployment=$("${kube[@]}" get deployments \
    -l "app.kubernetes.io/instance=$release,app.kubernetes.io/component=pdfparse" \
    -o 'jsonpath={.items[*].metadata.name}')
if [[ -z "$deployment" || "$deployment" == *' '* ]]; then
    printf 'Un seul Deployment PdfParse est attendu pour la release %s dans %s.\n' "$release" "$namespace" >&2
    exit 1
fi

# Réutilise l’image, les deux PVC et les Secrets de registre du Deployment.
image=$("${kube[@]}" get deployment "$deployment" \
    -o 'jsonpath={.spec.template.spec.containers[?(@.name=="pdfparse")].image}')
corpus_claim=$("${kube[@]}" get deployment "$deployment" \
    -o 'jsonpath={.spec.template.spec.volumes[?(@.name=="corpus-storage")].persistentVolumeClaim.claimName}')
models_claim=$("${kube[@]}" get deployment "$deployment" \
    -o 'jsonpath={.spec.template.spec.volumes[?(@.name=="models-storage")].persistentVolumeClaim.claimName}')
pull_secrets=$("${kube[@]}" get deployment "$deployment" \
    -o 'jsonpath={range .spec.template.spec.imagePullSecrets[*]}{.name}{"\n"}{end}')

# Arrête la copie si l’image ou l’un des deux PVC manque, notamment avec hostPath ou emptyDir.
if [[ -z "$image" || -z "$corpus_claim" || -z "$models_claim" ]]; then
    printf 'Le Deployment %s doit référencer une image PdfParse et deux PVC (corpus et modèles).\n' "$deployment" >&2
    exit 1
fi

# Crée un pod au nom généré par Kubernetes pour que chaque exécution ait son propre pod.
# Il utilise l’UID de PdfParse et le groupe du volume quand le pilote de stockage le prend en charge.
# Le conteneur reste en attente avec sleep et monte les deux PVC en écriture, sans jeton Kubernetes.
pod=$(
    {
        cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  generateName: kbv1-volume-upload-
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
    fsGroupChangePolicy: OnRootMismatch
    seccompProfile:
      type: RuntimeDefault
EOF
        # Insère les Secrets de lecture uniquement si le Deployment en référence.
        if [[ -n "$pull_secrets" ]]; then
            printf '  imagePullSecrets:\n'
            while IFS= read -r secret; do
                printf '    - name: "%s"\n' "$secret"
            done <<< "$pull_secrets"
        fi
        cat <<EOF
  containers:
    - name: upload
      image: "$image"
      command: ["sleep"]
      args: ["infinity"]
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop: [ALL]
      volumeMounts:
        - name: corpus
          mountPath: /data
        - name: models
          mountPath: /models
  volumes:
    - name: corpus
      persistentVolumeClaim:
        claimName: "$corpus_claim"
    - name: models
      persistentVolumeClaim:
        claimName: "$models_claim"
EOF
    } | "${kube[@]}" create -f - -o name
)
# Supprime uniquement le pod créé par cette exécution. Les PVC restent en place.
cleanup() {
    "${kube[@]}" delete "$pod" --ignore-not-found --wait=false
}
trap cleanup EXIT
# Une interruption déclenche aussi le nettoyage en passant par le gestionnaire EXIT.
trap 'exit 130' INT
trap 'exit 143' TERM

# Attend que l’image soit démarrée et que Kubernetes ait monté les volumes.
"${kube[@]}" wait --for=condition=Ready "$pod" --timeout=5m
# Vérifie les deux montages avant de démarrer le premier transfert.
"${kube[@]}" exec "$pod" -- sh -ec 'test -w /data && test -w /models'

# Envoie le contenu de chaque dossier par stdin, sans créer d’archive locale.
# L’extraction conserve l’arborescence et remplace les fichiers de même chemin.
for directory in data models; do
    printf 'Copie de ./%s vers /%s dans %s\n' "$directory" "$directory" "$pod"
    tar -C "$project_dir/$directory" -cf - . |
        "${kube[@]}" exec -i "$pod" -- tar -xf - -C "/$directory"
    "${kube[@]}" exec "$pod" -- ls -lah "/$directory"
done
