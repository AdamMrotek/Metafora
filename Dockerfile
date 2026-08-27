# The backend: FastAPI + Pipecat in one process, which is also the participant
# in every call. Nothing else runs here — the SFU is LiveKit Cloud and the
# portal is a static build on Vercel.
#
# Two stages so the uv cache and the build tools stay out of the shipped image.
# Expect ~700-800 MB: onnxruntime, llvmlite/numba and scipy are the weight, and
# the SmartTurn v3 model is an 8.7 MB ONNX file *inside* the pipecat wheel. That
# is the reason there is no model download at boot and no warm-up request.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies before source, so an edit to services/ does not re-resolve the
# world. `--frozen` because uv.lock is the contract; a Dockerfile that quietly
# resolves a different tree from CI is a Dockerfile that deploys something
# nobody tested.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim-bookworm

# No shell for it, no home to write to. The process serves an unauthenticated
# route to the public internet.
RUN useradd --system --create-home --shell /usr/sbin/nologin metafora

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=3000

COPY --from=build --chown=metafora:metafora /app/.venv /app/.venv
COPY --chown=metafora:metafora services/ ./services/
COPY --chown=metafora:metafora shared/ ./shared/

USER metafora
EXPOSE 3000

# `python -m`, exactly what `make api` runs, so the deployed entry point and the
# local one cannot drift.
CMD ["python", "-m", "services.core.app"]
