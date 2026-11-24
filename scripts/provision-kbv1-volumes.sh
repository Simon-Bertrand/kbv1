#!/usr/bin/env bash
# Approvisionne les PVC montés dans le Job Kubernetes depuis Gitea et S3.
# Une commande ou une étape de pipeline en échec interrompt le Job.
set -euo pipefail

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

# Le chemin S3 est obligatoire. Le point de montage du corpus vaut /data par défaut.
: "${DATA_S3_URI:?Renseigner DATA_S3_URI (s3://bucket/prefixe/)}"
data_dir=${DATA_DIR:-/data}

[[ "$DATA_S3_URI" =~ ^s3://[^/]+(/.*)?$ ]] || fail 'DATA_S3_URI doit désigner un bucket S3 et, si nécessaire, un préfixe.'
[[ -d "$data_dir" ]] || fail "Volume du corpus introuvable : $data_dir"
command -v aws >/dev/null || fail 'Commande manquante dans le conteneur : aws'

# Synchronise les clés du préfixe S3 en chemins relatifs au PVC.
# La taille et la date déterminent les fichiers à transférer. S3 fournit une somme de contrôle quand elle existe.
# Les tentatives de requête sont bornées. Les fichiers uniquement présents sur le PVC sont conservés.
sync_data() {
    AWS_RETRY_MODE="${AWS_RETRY_MODE:-standard}" AWS_MAX_ATTEMPTS="${AWS_MAX_ATTEMPTS:-8}" \
        aws s3 sync "${DATA_S3_URI%/}/" "$data_dir/" \
            --exact-timestamps --checksum-mode ENABLED --no-follow-symlinks --no-progress "$@"
}

# Le mode aperçu évalue uniquement les transferts S3, sans écrire ni utiliser le registre Gitea.
if [[ $# -eq 1 && "$1" == --plan-data ]]; then
    sync_data --dryrun
    exit 0
fi
[[ $# -eq 0 ]] || fail "Usage : bash $0 [--plan-data]"
[[ -w "$data_dir" ]] || fail "Volume du corpus non accessible en écriture : $data_dir"

# Le registre Generic et le manifeste décrivent les archives. Le fichier netrc fournit les identifiants à curl.
: "${GITEA_GENERIC_URL:?Renseigner GITEA_GENERIC_URL (URL HTTPS du registre Generic)}"
models_dir=${MODELS_DIR:-/models}
manifest=${MODELS_MANIFEST:-/config/models.tsv}
netrc=${GITEA_NETRC_FILE:-/secrets/gitea.netrc}

# Vérifie les accès et outils nécessaires avant de créer des fichiers temporaires.
[[ "$GITEA_GENERIC_URL" == https://?* ]] || fail 'GITEA_GENERIC_URL doit utiliser HTTPS.'
[[ -d "$models_dir" && -w "$models_dir" ]] || fail "Volume des modèles non accessible en écriture : $models_dir"
[[ -r "$manifest" ]] || fail "Manifeste des modèles inaccessible : $manifest"
[[ -r "$netrc" ]] || fail "Fichier netrc inaccessible : $netrc"
for command in curl tar sha256sum; do
    command -v "$command" >/dev/null || fail "Commande manquante dans le conteneur : $command"
done

# Réserve ces variables aux chemins temporaires créés par cette exécution.
# Le nettoyage retire l’archive et toute extraction partielle en cas d’échec ou d’interruption.
archive=
staging=
cleanup() {
    if [[ -n "$archive" ]]; then rm -f -- "$archive"; fi
    if [[ -n "$staging" ]]; then rm -rf -- "$staging"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Chaque ligne contient un SHA-256, un paquet, une version, un fichier et une cible catégorie/modèle.
# La boucle traite aussi la dernière ligne si elle ne se termine pas par un saut de ligne.
model_count=0
while read -r digest package version filename target extra || [[ -n "$digest" ]]; do
    [[ -z "$digest" || "$digest" == \#* ]] && continue
    # Valide les segments d’URL, l’extension de l’archive et le chemin relatif au PVC.
    [[ -z "$extra" &&
        "$digest" =~ ^[[:xdigit:]]{64}$ &&
        "$package" =~ ^[[:alnum:]][[:alnum:].+_-]*$ &&
        "$version" =~ ^[[:alnum:]][[:alnum:].+_-]*$ &&
        "$filename" =~ ^[[:alnum:]][[:alnum:].+_-]*\.tar(\.gz)?$ &&
        "$target" =~ ^[[:alnum:]_-]+/[[:alnum:]_.-]+$ &&
        "$target" != */. && "$target" != */.. ]] || fail "Entrée invalide dans $manifest : $package $target"
    model_count=$((model_count + 1))
    # Normalise la somme en minuscules avant la vérification et la comparaison du marqueur.
    digest=${digest,,}
    destination="$models_dir/$target"
    marker="$destination/.kbv1-archive-sha256"

    # Le marqueur permet de reprendre un Job interrompu. Un dossier existant d’une autre version est conservé.
    if [[ -e "$destination" || -L "$destination" ]]; then
        if [[ -f "$marker" && $(<"$marker") == "$digest" ]]; then
            printf 'Modèle déjà chargé : %s\n' "$target"
            continue
        fi
        fail "Le modèle $target est déjà présent avec un contenu différent ou non géré."
    fi

    printf 'Téléchargement du modèle %s (%s)\n' "$package" "$version"
    # Télécharge une seule archive à la fois dans TMPDIR et limite les transferts et redirections à HTTPS.
    archive=$(mktemp "${TMPDIR:-/tmp}/kbv1-model.XXXXXXXX")
    curl --fail --silent --show-error --location --retry 3 \
        --proto '=https' --proto-redir '=https' --netrc --netrc-file "$netrc" \
        --output "$archive" "${GITEA_GENERIC_URL%/}/$package/$version/$filename"
    # Contrôle l’intégrité de l’archive avant toute extraction sur le PVC.
    printf '%s  %s\n' "$digest" "$archive" |
        sha256sum --check --status || fail "Somme SHA-256 incorrecte pour $package/$version/$filename"

    # Extrait dans un répertoire temporaire du même volume. Le renommage rend le checkpoint complet visible en une seule opération.
    parent=${destination%/*}
    mkdir -p -- "$parent"
    staging=$(mktemp -d "$parent/.kbv1-model.XXXXXXXX")
    tar -xf "$archive" --no-same-owner --no-same-permissions -C "$staging"
    printf '%s\n' "$digest" > "$staging/.kbv1-archive-sha256"
    # Rend le dossier du checkpoint traversable par les conteneurs qui lisent le volume avec un autre UID.
    chmod a+rx "$staging"
    mv -T -- "$staging" "$destination"
    staging=
    rm -f -- "$archive"
    archive=
done < "$manifest"
(( model_count > 0 )) || fail "Aucun modèle dans $manifest."

# Lance la synchronisation du corpus après l’installation de tous les checkpoints du manifeste.
printf 'Synchronisation du corpus depuis S3 vers %s\n' "$data_dir"
sync_data
