.DEFAULT_GOAL := help

WEBUI_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
TARBALL_DIR ?= $(abspath $(WEBUI_DIR)/../../.tarball)

.PHONY: help deps deps-offline dev build build-ui export-tarballs docker-tarball
help:
	@echo 'make deps          Télécharge, met en cache et installe les dépendances locales'
	@echo 'make deps-offline  Réinstalle exclusivement depuis le cache local'
	@echo 'make dev           Démarre le serveur local'
	@echo 'make build         Produit la distribution unique et l’application Next.js'
	@echo 'make build-ui      Produit uniquement le package complet dans dist-ui/'
	@echo 'make export-tarballs Met à jour les bundles build/dev/prod sans reconstruire'
	@echo 'make docker-tarball Construit hors ligne et met à jour les bundles build/dev/prod'

deps:
	node scripts/prepare-deps.mjs

deps-offline:
	node scripts/prepare-deps.mjs --offline

dev:
	npm run dev

build:
	npm run build

build-ui:
	npm run build:ui

# Compatibility aliases: use the repository bundle workflow, never standalone tarballs.
docker-tarball:
	$(MAKE) -C "$(WEBUI_DIR)/../.." webui TARBALL_DIR="$(TARBALL_DIR)"

export-tarballs:
	$(MAKE) -C "$(WEBUI_DIR)/../.." webui-export TARBALL_DIR="$(TARBALL_DIR)"
