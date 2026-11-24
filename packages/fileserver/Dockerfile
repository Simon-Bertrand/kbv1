ARG BUILD_IMAGE_PREFIX=knowledgebase
FROM ${BUILD_IMAGE_PREFIX}/base-fileserver:local AS nginx-base

FROM ${BUILD_IMAGE_PREFIX}/build-fileserver:local AS modzip-build

# Build against the same Alpine NGINX as the runtime. The packaged mod_zip
# 1.3.0 needs a NULL-upstream guard and correct dynamic filter ordering.
COPY mod_zip-njs.patch /build/mod_zip-njs.patch
RUN set -eux; \
    version="$(nginx -v 2>&1 | sed 's|nginx version: nginx/||')"; \
    sha256sum -c /build/SHA256SUMS; \
    tar -xzf /build/nginx.tar.gz -C /build; \
    tar -xzf /build/mod_zip.tar.gz -C /build; \
    patch -d /build/mod_zip-1.3.0 -p1 < /build/mod_zip-njs.patch; \
    cd "/build/nginx-${version}"; \
    ./configure --with-compat --add-dynamic-module=/build/mod_zip-1.3.0; \
    make -j"$(getconf _NPROCESSORS_ONLN)" modules; \
    cp objs/ngx_http_zip_module.so /build/ngx_http_zip_module.so

FROM nginx-base

RUN mkdir -p /srv/data /usr/share/nginx/html /var/cache/nginx /run/nginx
COPY --from=modzip-build /build/ngx_http_zip_module.so /usr/lib/nginx/modules/ngx_http_zip_module.so

COPY file-delivery.conf /etc/nginx/file-delivery.conf
COPY nginx.conf /etc/nginx/nginx.conf
COPY njs/explorer.js /etc/nginx/njs/explorer.js
COPY web/ /usr/share/nginx/html/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN nginx -t

EXPOSE 8080

STOPSIGNAL SIGQUIT

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["nginx", "-e", "/dev/stderr", "-g", "daemon off;"]
