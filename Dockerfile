FROM ghcr.io/berriai/litellm-non_root:v1.83.14-stable

USER root
COPY transformation.py /tmp/transformation.py
RUN set -eux; \
    target="$(find /app/.venv -path '*chatgpt/responses/transformation.py' | head -n 1)"; \
    test -n "$target"; \
    cp /tmp/transformation.py "$target"; \
    rm /tmp/transformation.py
USER nonroot
