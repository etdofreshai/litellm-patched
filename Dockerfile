FROM ghcr.io/berriai/litellm-non_root:v1.83.14-stable

USER root

# Existing source patches
COPY transformation.py /tmp/transformation.py
COPY factory_patch.py /tmp/factory_patch.py
RUN set -eux; \
    target="$(find /app/.venv -path '*chatgpt/responses/transformation.py' | head -n 1)"; \
    test -n "$target"; \
    cp /tmp/transformation.py "$target"; \
    factory="$(find /app/.venv -path '*prompt_templates/factory.py' | head -n 1)"; \
    test -n "$factory"; \
    cat /tmp/factory_patch.py >> "$factory"; \
    echo "" >> "$factory"; \
    echo "# Override original with patched version" >> "$factory"; \
    echo "map_system_message_pt = patched_map_system_message_pt" >> "$factory"; \
    rm /tmp/transformation.py /tmp/factory_patch.py

# Add `node` user (uid 1000) + Codex CLI so `codex login` writes to
# /home/node/.codex/auth.json with predictable ownership when the
# /home/node volume is mounted from Dokploy.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg sudo; \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    rm -rf /var/lib/apt/lists/*; \
    if id -u nonroot >/dev/null 2>&1; then userdel -r nonroot 2>/dev/null || userdel nonroot; fi; \
    useradd -m -u 1000 -s /bin/bash node; \
    echo "node ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/node; \
    chmod 0440 /etc/sudoers.d/node; \
    npm install -g @openai/codex; \
    codex --version; \
    chown -R node:node /app

USER node
