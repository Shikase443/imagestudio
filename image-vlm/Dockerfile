# syntax=docker/dockerfile:1
FROM nvidia/cuda:13.0.0-devel-ubuntu24.04@sha256:38c2598ded0fd3ed2b0b13351543fde493faba43ae580f81f0a9ab58d76fdd2d AS build
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
    && mkdir -p /build-info/licenses \
    && cp LICENSE /build-info/licenses/backend-LICENSE.txt \
    && if [ ! -f ggml/LICENSE ]; then true; else cp ggml/LICENSE /build-info/licenses/ggml-LICENSE.txt; fi \
    && git rev-parse HEAD > /build-info/source-commit.txt \
    && cp build/CMakeCache.txt /build-info/ \
    && nvcc --version > /build-info/cuda-version.txt \
    && cuobjdump --list-elf build/bin/llama-server > /build-info/cuda-architectures.txt \
    && grep -q sm_121a /build-info/cuda-architectures.txt

FROM nvidia/cuda:13.0.0-runtime-ubuntu24.04@sha256:532381a7bf759bbe4d9c783468bc2cc4766cf656da5814fb04e049dc58fe268b
LABEL org.opencontainers.image.source="https://github.com/ggml-org/llama.cpp" \
      org.opencontainers.image.revision="bfe65e1dfa8f1afed8a2158e83e5d2a3d6db5444"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 libssl3t64 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /models /output
COPY --from=build /src/llama.cpp/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=build /build-info /usr/local/share/llama-build
# NVIDIA Container Toolkit supplies libcuda.so.1 when the GPU container starts.
RUN ldd /usr/local/bin/llama-server > /usr/local/share/llama-build/ldd-build.txt \
    && cat /usr/local/share/llama-build/ldd-build.txt \
    && ! grep 'not found' /usr/local/share/llama-build/ldd-build.txt | grep -v 'libcuda.so.1 => not found'
WORKDIR /output
EXPOSE 8002
HEALTHCHECK --interval=10s --timeout=5s --start-period=30m --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8002/health > /dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/llama-server"]
