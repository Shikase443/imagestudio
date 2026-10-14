# syntax=docker/dockerfile:1
# Both CUDA images are pinned to their linux/arm64 manifests.
FROM nvidia/cuda:13.0.0-devel-ubuntu24.04@sha256:38c2598ded0fd3ed2b0b13351543fde493faba43ae580f81f0a9ab58d76fdd2d AS build

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
    && mkdir -p /build-info/licenses \
    && cp LICENSE /build-info/licenses/backend-LICENSE.txt \
    && if [ ! -f ggml/LICENSE ]; then true; else cp ggml/LICENSE /build-info/licenses/ggml-LICENSE.txt; fi \
    && git rev-parse HEAD > /build-info/source-commit.txt \
    && git submodule status > /build-info/submodules.txt \
    && cp build/CMakeCache.txt /build-info/ \
    && nvcc --version > /build-info/cuda-version.txt \
    && cuobjdump --list-elf build/bin/sd-server > /build-info/cuda-architectures.txt \
    && grep -q sm_121a /build-info/cuda-architectures.txt

FROM nvidia/cuda:13.0.0-runtime-ubuntu24.04@sha256:532381a7bf759bbe4d9c783468bc2cc4766cf656da5814fb04e049dc58fe268b
LABEL org.opencontainers.image.source="https://github.com/leejet/stable-diffusion.cpp" \
      org.opencontainers.image.revision="c150a6bafeb21824f30b813ae1212321a5d131fd"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /models /output

COPY --from=build /src/stable-diffusion.cpp/build/bin/sd-server /usr/local/bin/sd-server
COPY --from=build /build-info /usr/local/share/sd-build
# libcuda.so.1 is supplied by NVIDIA Container Toolkit at GPU runtime, not build time.
# The separate GPU-enabled validation must resolve every library and run --help.
RUN ldd /usr/local/bin/sd-server > /usr/local/share/sd-build/ldd-build.txt \
    && cat /usr/local/share/sd-build/ldd-build.txt \
    && ! grep 'not found' /usr/local/share/sd-build/ldd-build.txt | grep -v 'libcuda.so.1 => not found'

WORKDIR /output
EXPOSE 8001
# This endpoint is verified in routes_openai.cpp at the pinned commit.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30m --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8001/v1/models > /dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/sd-server"]
