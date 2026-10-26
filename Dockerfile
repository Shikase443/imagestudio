# syntax=docker/dockerfile:1
# Both CUDA images are pinned to their linux/arm64 manifests.
FROM nvidia/cuda:13.0.0-devel-ubuntu24.04@sha256:38c2598ded0fd3ed2b0b13351543fde493faba43ae580f81f0a9ab58d76fdd2d AS gen-build

RUN test "$(uname -m)" = aarch64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git ninja-build \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src/stable-diffusion.cpp
# Submodule revisions are supplied by this exact parent commit.
RUN git init \
    && git remote add origin https://github.com/leejet/stable-diffusion.cpp.git \
    && git fetch --depth 1 origin c150a6bafeb21824f30b813ae1212321a5d131fd \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = c150a6bafeb21824f30b813ae1212321a5d131fd \
    && git submodule update --init --depth 1 ggml

ARG BUILD_JOBS=8
# SM 121a needs architecture-specific Blackwell code; no GPU is needed for compilation.
# PNG/JPEG API use does not require the video codecs or the browser frontend.
RUN nvcc --version \
    && cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CUDA_ARCHITECTURES=121a-real \
        -DSD_CUDA=ON \
        -DSD_BUILD_EXAMPLES=ON \
        -DSD_SERVER_BUILD_FRONTEND=OFF \
        -DSD_WEBP=OFF \
        -DSD_WEBM=OFF \
        -DGGML_NATIVE=OFF \
        -DGGML_CUDA_FA_ALL_QUANTS=ON \
    && cmake --build build --target sd-server --parallel "${BUILD_JOBS}" \
    && mkdir -p /build-info \
    && git rev-parse HEAD > /build-info/source-commit.txt \
    && git submodule status > /build-info/submodules.txt \
    && cp build/CMakeCache.txt /build-info/ \
    && nvcc --version > /build-info/cuda-version.txt \
    && cuobjdump --list-elf build/bin/sd-server > /build-info/cuda-architectures.txt \
    && grep -q sm_121a /build-info/cuda-architectures.txt

RUN mkdir -p /build-info/licenses \
    && cp LICENSE /build-info/licenses/backend-LICENSE.txt \
    && if [ ! -f ggml/LICENSE ]; then true; else cp ggml/LICENSE /build-info/licenses/ggml-LICENSE.txt; fi

FROM nvidia/cuda:13.0.0-devel-ubuntu24.04@sha256:38c2598ded0fd3ed2b0b13351543fde493faba43ae580f81f0a9ab58d76fdd2d AS vlm-build
RUN test "$(uname -m)" = aarch64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git ninja-build libssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/llama.cpp
RUN git init \
    && git remote add origin https://github.com/ggml-org/llama.cpp.git \
    && git fetch --depth 1 origin bfe65e1dfa8f1afed8a2158e83e5d2a3d6db5444 \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = bfe65e1dfa8f1afed8a2158e83e5d2a3d6db5444
ARG BUILD_JOBS=8
# Build only the API server, with static project libraries and SM 121a device code.
RUN cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CUDA_ARCHITECTURES=121a-real \
        -DGGML_CUDA=ON -DGGML_NATIVE=OFF \
        -DGGML_CUDA_FA_ALL_QUANTS=ON \
        -DBUILD_SHARED_LIBS=OFF -DGGML_BACKEND_DL=OFF \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
        -DLLAMA_BUILD_APP=OFF -DLLAMA_BUILD_TOOLS=ON \
        -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF \
    && cmake --build build --target llama-server --parallel "${BUILD_JOBS}" \
    && mkdir -p /build-info \
    && git rev-parse HEAD > /build-info/source-commit.txt \
    && cp build/CMakeCache.txt /build-info/ \
    && nvcc --version > /build-info/cuda-version.txt \
    && cuobjdump --list-elf build/bin/llama-server > /build-info/cuda-architectures.txt \
    && grep -q sm_121a /build-info/cuda-architectures.txt

RUN mkdir -p /build-info/licenses \
    && cp LICENSE /build-info/licenses/backend-LICENSE.txt \
    && if [ ! -f ggml/LICENSE ]; then true; else cp ggml/LICENSE /build-info/licenses/ggml-LICENSE.txt; fi


# Build stable CPython without changing the CUDA base or adding host packages.
FROM nvidia/cuda:13.0.0-runtime-ubuntu24.04@sha256:532381a7bf759bbe4d9c783468bc2cc4766cf656da5814fb04e049dc58fe268b AS python-build
ENV DEBIAN_FRONTEND=noninteractive
COPY runtime/update_os_packages.sh /usr/local/bin/update-os-packages
RUN sh /usr/local/bin/update-os-packages \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl libbz2-dev libffi-dev libgdbm-dev \
        liblzma-dev libncurses-dev libreadline-dev libsqlite3-dev libssl-dev \
        libzstd-dev pkg-config uuid-dev xz-utils zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
ARG PYTHON_VERSION=3.15.2
ARG PYTHON_SHA256=8b5536d280142ecf6cdfe15d12a21ba1c65e300bbf7b28a8d0cdb849575797de
ARG PYTHON_BUILD_JOBS=8
RUN curl --fail --show-error --location --retry 3 \
        "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz" -o /tmp/python.tar.xz \
    && echo "${PYTHON_SHA256}  /tmp/python.tar.xz" | sha256sum --check --strict \
    && mkdir /tmp/python-source \
    && tar -xJf /tmp/python.tar.xz --strip-components=1 -C /tmp/python-source \
    && cd /tmp/python-source \
    && ./configure --prefix=/opt/python --with-ensurepip=install --disable-test-modules --without-system-libmpdec \
    && make -j "${PYTHON_BUILD_JOBS}" \
    && make install \
    && /opt/python/bin/python3 -c "import ssl, sqlite3, bz2, lzma, ctypes, hashlib, zlib, compression.zstd, _decimal" \
    && mkdir -p /opt/python-build-info \
    && printf '%s\n' "${PYTHON_VERSION}" > /opt/python-build-info/version.txt \
    && printf '%s\n' "${PYTHON_SHA256}" > /opt/python-build-info/source-sha256.txt \
    && cp LICENSE /opt/python-build-info/LICENSE.txt \
    && rm -rf /tmp/python-source /tmp/python.tar.xz


FROM nvidia/cuda:13.0.0-runtime-ubuntu24.04@sha256:532381a7bf759bbe4d9c783468bc2cc4766cf656da5814fb04e049dc58fe268b AS runtime
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH HOME=/tmp \
    IMAGE_GEN_URL=http://127.0.0.1:8001 IMAGE_VLM_URL=http://127.0.0.1:8002 \
    IMAGE_API_URL=http://127.0.0.1:8000 TELEMETRY_DIR=/output/telemetry
COPY runtime/update_os_packages.sh /usr/local/bin/update-os-packages
RUN sh /usr/local/bin/update-os-packages \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl libbz2-1.0 libffi8 libgdbm6t64 libgomp1 liblzma5 \
        libncursesw6 libreadline8t64 libsqlite3-0 libssl3t64 libzstd1 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /models /output
COPY --from=python-build /opt/python /opt/python
COPY --from=python-build /opt/python-build-info /usr/local/share/python-build
RUN /opt/python/bin/python3 -m venv /opt/venv \
    && python -m pip install --no-cache-dir --upgrade pip==26.2.2
COPY --from=gen-build /src/stable-diffusion.cpp/build/bin/sd-server /usr/local/bin/sd-server
COPY --from=gen-build /build-info /usr/local/share/sd-build
COPY --from=vlm-build /src/llama.cpp/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=vlm-build /build-info /usr/local/share/llama-build
WORKDIR /app
COPY gateway/requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt \
    && ldd /usr/local/bin/sd-server > /tmp/sd-ldd.txt \
    && ldd /usr/local/bin/llama-server > /tmp/llama-ldd.txt \
    && ! grep 'not found' /tmp/sd-ldd.txt /tmp/llama-ldd.txt | grep -v 'libcuda.so.1 => not found'
COPY gateway /app/gateway
COPY webui /app/webui
COPY runtime /app/runtime
COPY config /app/config
COPY tools/prepare_models.py /app/tools/prepare_models.py
EXPOSE 8000 8080
HEALTHCHECK --interval=10s --timeout=12s --start-period=30m --retries=3 \
    CMD python /app/runtime/healthcheck.py
ENTRYPOINT ["python", "/app/runtime/launcher.py"]
