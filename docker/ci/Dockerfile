FROM knowledgebase/ci-helm-source:latest AS helm
FROM quay.io/buildah/stable:latest
COPY --from=helm /usr/bin/helm /usr/local/bin/helm
RUN buildah --version && helm version --short
