#!/bin/sh
set -e

KB_ENV="${KB_ENV:-dev}"

if [ "$KB_ENV" = "production" ] || [ "$KB_ENV" = "prod" ]; then
    if [ ${#FILESERVER_JWT_SECRET} -lt 32 ] || [ "$FILESERVER_JWT_SECRET" = "knowledgebase-default-fileserver-jwt-secret" ]; then
        echo >&2 "[FATAL ERROR] [PROD MODE] FILESERVER_JWT_SECRET doit être défini avec un secret fort en production."
        exit 1
    fi
    if [ -z "$FILESERVER_UI_SECRET" ] || [ "$FILESERVER_UI_SECRET" = "knowledgebase-default-fileserver-ui-secret" ]; then
        echo >&2 "[FATAL ERROR] [PROD MODE] FILESERVER_UI_SECRET doit être défini avec un secret fort en production."
        exit 1
    fi
    clean_hosts=$(echo "$ALLOWED_HOSTS" | tr -d '[:space:]')
    case ",$clean_hosts," in
        *",*,"*|",,"|",")
            echo >&2 "[FATAL ERROR] [PROD MODE] ALLOWED_HOSTS doit être explicitement restreint en production (ne peut contenir '*' ou être vide)."
            exit 1
            ;;
    esac
    if [ -n "$FILESERVER_DEV_USER" ] || [ -n "$FILESERVER_DEV_PASSWORD" ]; then
        echo >&2 "[STARTUP WARNING] [PROD MODE] FILESERVER_DEV_USER / FILESERVER_DEV_PASSWORD sont définis mais sont strictement ignorés en production (l'UI explorateur est désactivée)."
    fi
    if [ ! -d "/srv/data" ]; then
        echo >&2 "[FATAL ERROR] [PROD MODE] Le point de montage /srv/data est manquant ou n'est pas un répertoire."
        exit 1
    fi
    # Vérification stricte que /srv/data est bien monté en lecture seule en production
    if touch /srv/data/.ro_test 2>/dev/null; then
        rm -f /srv/data/.ro_test
        echo >&2 "[FATAL ERROR] [PROD MODE] Le répertoire /srv/data (DATA_DIR) DOIT être monté en lecture seule (read_only: true). Écriture détectée !"
        exit 1
    fi
else
    if [ -d "/srv/data" ]; then
        if touch /srv/data/.ro_test 2>/dev/null; then
            rm -f /srv/data/.ro_test
            echo >&2 "[SECURITY WARNING] [DEV MODE] Le répertoire /srv/data (DATA_DIR) n'est pas monté en lecture seule (read_only: true)."
        fi
    fi
    if [ -z "$FILESERVER_JWT_SECRET" ] || [ "$FILESERVER_JWT_SECRET" = "knowledgebase-default-fileserver-jwt-secret" ]; then
        echo >&2 "[SECURITY WARNING] [DEV MODE] FILESERVER_JWT_SECRET non configuré ! Utilisation d'un secret par défaut non sécurisé."
    fi
    if [ -z "$FILESERVER_UI_SECRET" ] || [ "$FILESERVER_UI_SECRET" = "knowledgebase-default-fileserver-ui-secret" ]; then
        echo >&2 "[SECURITY WARNING] [DEV MODE] FILESERVER_UI_SECRET non configuré ! Utilisation d'un secret par défaut non sécurisé."
    fi
    if [ -z "$ALLOWED_HOSTS" ] || [ "$ALLOWED_HOSTS" = "*" ]; then
        echo >&2 "[STARTUP WARNING] [DEV MODE] ALLOWED_HOSTS='*' actif sur le fileserver."
    fi
    if [ -n "$FILESERVER_DEV_USER" ] || [ -n "$FILESERVER_DEV_PASSWORD" ]; then
        echo >&2 "[STARTUP INFO] [DEV MODE] Basic Auth configuré pour l'UI Fileserver sur '/' (utilisateur: ${FILESERVER_DEV_USER:-dev})."
    else
        echo >&2 "[STARTUP INFO] [DEV MODE] Basic Auth actif pour l'UI Fileserver sur '/' avec identifiants par défaut (dev/dev)."
    fi
fi

exec "$@"
