FROM ghcr.io/berriai/litellm-non_root:v1.83.14-stable

USER root
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
USER nonroot
