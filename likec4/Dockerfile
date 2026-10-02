FROM mcr.microsoft.com/playwright:v1.56.1-noble

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV NODE_ENV=production
ENV PNPM_HOME=/usr/local/share/pnpm
ENV PATH=${PNPM_HOME}/bin:/usr/local/bin:${PATH}

ARG LIKEC4_VER=latest
ARG PNPM_VERSION=12.6.0

RUN set -eux; \
    curl -fsSL --retry 3 https://get.pnpm.io/install.sh -o /tmp/install-pnpm.sh; \
    env PNPM_VERSION="${PNPM_VERSION}" PNPM_HOME="${PNPM_HOME}" SHELL=/bin/bash bash /tmp/install-pnpm.sh; \
    rm -f /tmp/install-pnpm.sh; \
    pnpm --version

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends graphviz; \
    rm -rf /var/lib/apt/lists/*; \
    if [ -n "${LIKEC4_VER:-}" ]; then \
        pnpm add --global --allow-build=esbuild "likec4@${LIKEC4_VER}"; \
    else \
        pnpm add --global --allow-build=esbuild likec4; \
    fi; \
    LIKEC4_BIN="${PNPM_HOME}/bin/likec4"; \
    if [ -f "$LIKEC4_BIN" ]; then \
        ln -sf "$LIKEC4_BIN" /usr/local/bin/likec4; \
    else \
        echo "likec4 binary not found at $LIKEC4_BIN"; \
        find /usr/local -type f -name likec4 -maxdepth 6 2>/dev/null || true; \
    fi

RUN set -eux; \
    mkdir -p /home/pwuser/.cache/likec4-vite; \
    LIKEC4_ROOT="$(find "${PNPM_HOME}/global" -path '*/node_modules/likec4' -print -quit)"; \
    test -n "$LIKEC4_ROOT"; \
    test -d "$LIKEC4_ROOT/node_modules"; \
    rm -rf "${LIKEC4_ROOT}/node_modules/.vite"; \
    ln -s /home/pwuser/.cache/likec4-vite "${LIKEC4_ROOT}/node_modules/.vite"; \
    chown -R pwuser:pwuser /home/pwuser/.cache/likec4-vite

WORKDIR /opt/likec4-worker
COPY worker/package.json ./
RUN pnpm install --prod
COPY worker ./
RUN mkdir -p /data \
    && chown -R pwuser:pwuser /opt/likec4-worker /data

WORKDIR /data
USER pwuser

EXPOSE 4173 5173 24678 9000
