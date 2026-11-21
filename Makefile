ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
COMPOSE_FILE ?= $(ROOT)/compose.dev.yaml
COMPOSE := docker compose --project-directory "$(ROOT)" -f "$(COMPOSE_FILE)"
TARBALL_DIR ?= $(ROOT)/.tarball
TARBALL ?= $(TARBALL_DIR)/pdfparse-offline.tar
PDFPARSE_IMAGE := kbv1/pdfparse:local
IMAGE ?= $(PDFPARSE_IMAGE)
SERVICES ?=
DEV_SERVICES := postgres fileserver pdfparse encoder indexer km-api km-frontend webui

.DEFAULT_GOAL := help
.PHONY: help config dev rebuild-dev up-dev logs ps stop $(DEV_SERVICES) docker docker-build docker-tarball

help:
	@printf '%s\n' \
	  'make dev           Construire et démarrer la stack de développement' \
	  'make rebuild-dev   Construire les images sans démarrer les services' \
	  'make up-dev        Démarrer avec les images locales déjà construites' \
	  'make webui         Construire et démarrer la WebUI et ses dépendances' \
	  'make config        Valider la configuration Compose' \
	  'make ps|logs|stop  Gérer les services de développement' \
	  'make docker        Construire et exporter l’image PdfParse' \
	  'SERVICES="webui km-api" permet de sélectionner des services.'

config:
	$(COMPOSE) config --quiet

dev:
	$(COMPOSE) up -d --build $(SERVICES)

rebuild-dev:
	$(COMPOSE) build $(SERVICES)

up-dev:
	$(COMPOSE) up -d --pull never --no-build $(SERVICES)

$(DEV_SERVICES):
	$(COMPOSE) up -d --build $@

ps logs stop:
	$(COMPOSE) $@ $(SERVICES)

docker: docker-tarball

docker-build:
	$(COMPOSE) build pdfparse
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
