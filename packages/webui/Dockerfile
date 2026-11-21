FROM knowledgebase/build-webui:local AS build
WORKDIR /app
COPY . .
RUN sha256sum -c /opt/offline/webui.sha256
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM knowledgebase/base-webui:local
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=4173
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 4173
CMD ["node", "server.js"]
