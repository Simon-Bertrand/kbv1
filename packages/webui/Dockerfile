ARG BUILD_IMAGE_PREFIX=knowledgebase
FROM ${BUILD_IMAGE_PREFIX}/build-webui:latest AS build
WORKDIR /app
COPY . .
RUN sha256sum -c /opt/offline/webui.sha256
ENV NEXT_TELEMETRY_DISABLED=1
ARG WEBUI_GIT_COMMIT
ARG WEBUI_GIT_BRANCH
RUN npm run prebuild && ./node_modules/.bin/next build --webpack

FROM ${BUILD_IMAGE_PREFIX}/base-webui:latest
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=4173
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 4173
CMD ["node", "server.js"]
