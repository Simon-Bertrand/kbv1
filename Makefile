ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
COMPOSE_FILE ?= $(ROOT)/compose.dev.yaml
COMPOSE := docker compose --project-directory "$(ROOT)" -f "$(COMPOSE_FILE)"
TARBALL_DIR ?= $(ROOT)/.tarball
WHEELHOUSE_DIR ?= $(ROOT)/.wheelhouse
COMPOSE_PARALLEL_LIMIT ?= 1
PDFPARSE_WHEEL_PROFILE ?= pdfparse
export WHEELHOUSE_DIR COMPOSE_PARALLEL_LIMIT PDFPARSE_WHEEL_PROFILE
PYTHON ?= python3.12
BUILD_COMPOSE = $(COMPOSE)
TARBALL ?= $(TARBALL_DIR)/pdfparse-offline.tar
PDFPARSE_IMAGE := kbv1/pdfparse:local
IMAGE ?= $(PDFPARSE_IMAGE)
SERVICES ?=
DEV_SERVICES := postgres fileserver pdfparse encoder indexer km-api km-frontend webui

.DEFAULT_GOAL := help
.PHONY: help config prepare-build prepare-bases import-build dev rebuild-dev up-dev logs ps stop $(DEV_SERVICES) docker docker-build docker-tarball

help:
	@printf '%s\n' \
	  'make dev           Construire et démarrer la stack de développement' \
	  'make prepare-build Préparer les bases Docker et la wheelhouse sur une machine connectée' \
	  'make prepare-bases Préparer les images de base pour Docker Compose' \
	  'make import-build   Importer les archives de dépendances Docker' \
	  'make rebuild-dev   Construire les images sans démarrer les services' \
	  'make up-dev        Démarrer avec les images locales déjà construites' \
	  'make webui         Construire et démarrer la WebUI et ses dépendances' \
	  'make config        Valider la configuration Compose' \
	  'make ps|logs|stop  Gérer les services de développement' \
	  'make docker        Construire et exporter l’image PdfParse' \
	  'SERVICES="webui km-api" permet de sélectionner des services.'

prepare-build:
	cd "$(ROOT)" && sha256sum -c docker/python/inputs.sha256
	cd "$(ROOT)" && "$(PYTHON)" -m scripts.docker.build_bundle prepare --directory "$(TARBALL_DIR)/build" \
	  --wheelhouse "$(WHEELHOUSE_DIR)" --pdfparse-profile "$(PDFPARSE_WHEEL_PROFILE)"

prepare-bases:
	cd "$(ROOT)" && sha256sum -c docker/python/inputs.sha256
	cd "$(ROOT)" && "$(PYTHON)" -m scripts.docker.build_bundle bases \
	  --wheelhouse "$(WHEELHOUSE_DIR)" --pdfparse-profile "$(PDFPARSE_WHEEL_PROFILE)"

import-build:
	cd "$(ROOT)" && "$(PYTHON)" -m scripts.docker.build_bundle load --directory "$(TARBALL_DIR)/build"

config:
	$(COMPOSE) config --quiet

dev:
	$(BUILD_COMPOSE) up -d --build $(SERVICES)

rebuild-dev:
	$(BUILD_COMPOSE) build $(SERVICES)

up-dev:
	$(COMPOSE) up -d --pull never --no-build $(SERVICES)

$(DEV_SERVICES):
	$(BUILD_COMPOSE) up -d --build $@

ps logs stop:
	$(COMPOSE) $@ $(SERVICES)

docker: docker-tarball

docker-build:
	$(BUILD_COMPOSE) build pdfparse
ifneq ($(IMAGE),$(PDFPARSE_IMAGE))
	docker image tag "$(PDFPARSE_IMAGE)" "$(IMAGE)"
endif

docker-tarball: docker-build
	mkdir -p "$(dir $(TARBALL))"
	@set -eu; \
	 temporary=$$(mktemp "$(TARBALL).XXXXXX"); \
	 trap 'rm -f "$$temporary"' EXIT; \
	 docker image save --output "$$temporary" "$(IMAGE)"; \
	 mv "$$temporary" "$(TARBALL)"; \
	 cd "$(dir $(TARBALL))"; \
	 sha256sum "$(notdir $(TARBALL))" > "$(notdir $(TARBALL)).sha256"; \
	 echo "Archive : $(TARBALL)"
