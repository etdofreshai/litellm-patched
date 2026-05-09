FROM ghcr.io/berriai/litellm-non_root:v1.83.14-stable

USER root

# Patch chatgpt non-streaming response aggregation + Anthropic system-message
# flattening in litellm core.
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

# Switch runtime user from upstream nobody (uid 65534) to uid 1000 so this
# container shares ownership conventions with the ai-sessions / CCRCM
# containers. The /home/node volume can then be written by `codex login`
# from any of those containers and read here without chown gymnastics.
ENV HOME=/home/node
RUN set -eux; \
    mkdir -p /home/node; \
    PRISMA_PATH="$(python -c 'import os, prisma; print(os.path.dirname(prisma.__file__))' 2>/dev/null || true)"; \
    LITELLM_PROXY_EXTRAS_PATH="$(python -c 'import os, litellm_proxy_extras; print(os.path.dirname(litellm_proxy_extras.__file__))' 2>/dev/null || true)"; \
    chown -R 1000:1000 \
        /home/node \
        /app \
        /var/lib/litellm/ui \
        /var/lib/litellm/assets \
        /nonexistent; \
    [ -n "$PRISMA_PATH" ] && chown -R 1000:1000 "$PRISMA_PATH" || true; \
    [ -n "$LITELLM_PROXY_EXTRAS_PATH" ] && chown -R 1000:1000 "$LITELLM_PROXY_EXTRAS_PATH" || true

USER 1000
