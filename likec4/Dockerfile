FROM mcr.microsoft.com/playwright:v1.65.0-noble@sha256:6e3fb709361521eb27161606e6da6d2674e5e89fa16516cb37d6fdd91372273d AS runtime
# Existing layout dependency, pinned rather than removed during isolation.
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz=2.42.2-9ubuntu0.1 libbrotli1=1.1.0-2ubuntu0.1 \
    && rm -rf /var/lib/apt/lists/* \
    # npm/corepack/yarn are build tooling, unused by either runtime target.
    && rm -rf /usr/lib/node_modules/npm /usr/lib/node_modules/corepack /opt/yarn \
    && rm -f /usr/bin/npm /usr/bin/npx /usr/bin/corepack /usr/bin/yarn /usr/bin/yarnpkg /usr/local/bin/yarn /usr/local/bin/yarnpkg

FROM runtime AS toolchain

# Version and SHA-512 are checked before executing the package manager.
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) platform=x64; integrity='qFWBneHJAJ73W4whtbaFOL1M/7DBC6ILHXuxc7ZPtEhfPuT1zeZiGrmKHoMAfJA+mcm6xhOFljqVTUS+00Jabw==' ;; \
      arm64) platform=arm64; integrity='8h2sNoIhHDpHDYqZZCh9PXr6krqW10btb0GHVGuw710muDHlFAHMtUG2ogmaHge6zJG1xgY4Y7mwX5g/LWvrYg==' ;; \
      *) exit 1 ;; \
    esac; \
    curl -fsSL --retry 3 "https://registry.npmjs.org/@pnpm/exe.linux-${platform}/-/exe.linux-${platform}-12.6.0.tgz" -o /tmp/pnpm.tgz; \
    test "$(openssl dgst -sha512 -binary /tmp/pnpm.tgz | openssl base64 -A)" = "$integrity"; \
    mkdir /opt/pnpm; \
    tar -xzf /tmp/pnpm.tgz -C /opt/pnpm --strip-components=1; \
    rm /tmp/pnpm.tgz; \
    ln -s /opt/pnpm/pnpm /usr/local/bin/pnpm; \
    test "$(pnpm --version)" = '12.6.0'

WORKDIR /opt/likec4
COPY worker/package.json worker/pnpm-lock.yaml worker/pnpm-workspace.yaml ./
RUN pnpm install --prod --frozen-lockfile \
    && test "$(node -p "require('likec4/package.json').version")" = '1.60.0' \
    && test "$(node -p "require('likec4/package.json').dependencies.playwright")" = '1.64.0'
# Vite may optimize bundled dependencies even when application code is immutable.
RUN set -eux; \
    root="$(readlink -f node_modules/likec4)"; \
    rm -rf "$root/node_modules/.vite"; \
    ln -s /tmp/likec4-vite "$root/node_modules/.vite"

FROM runtime AS broker
ARG APP_UID=1000
ARG APP_GID=1000
RUN set -eux; \
    test "$APP_UID" -gt 0; test "$APP_GID" -gt 0; \
    if ! getent group "$APP_GID" >/dev/null; then groupadd --gid "$APP_GID" likec4-runtime; fi; \
    if ! getent passwd "$APP_UID" >/dev/null; then \
      useradd --uid "$APP_UID" --gid "$APP_GID" --home-dir /tmp --no-create-home --shell /usr/sbin/nologin likec4-runtime; \
    fi
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 NODE_ENV=production HOME=/tmp XDG_CACHE_HOME=/tmp/cache
WORKDIR /opt/likec4
COPY server.js ./
COPY ui/index.html ui/viewer.html ui/security.js ./ui/
COPY worker/*.js ./worker/
# No writable source tree or repository-wide COPY. Runtime writes go to tmpfs.
RUN chmod -R a-w /opt/likec4 \
    && mkdir -p /var/likec4-exports \
    && chown "${APP_UID}:${APP_GID}" /var/likec4-exports
USER ${APP_UID}:${APP_GID}
EXPOSE 4173 9000
CMD ["node", "/opt/likec4/server.js"]

FROM broker AS renderer
COPY --from=toolchain /opt/likec4/node_modules ./node_modules
COPY --from=toolchain /opt/likec4/package.json /opt/likec4/pnpm-lock.yaml ./
CMD ["node", "/opt/likec4/worker/renderer.js"]
