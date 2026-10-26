#!/bin/sh
# Refresh Ubuntu packages while retaining every installed CUDA/NVIDIA package.
set -eu
mkdir -p /usr/local/share/image-studio
cuda_packages() {
    dpkg-query -W -f='${binary:Package}\t${Version}\n' | \
        awk '$1 ~ /^(cuda-|nvidia-|nsight-|libcublas|libcufft|libcufile|libcurand|libcusolver|libcusparse|libcupti|libcudnn|libnpp|libnvjitlink|libnvfatbin|libnvjpeg|libnvvm|libnccl)/' | sort
}
cuda_packages > /tmp/cuda-before.lock
cut -f1 /tmp/cuda-before.lock | xargs -r apt-mark hold
apt-get update
apt-get -y -o APT::Get::Always-Include-Phased-Updates=true upgrade --with-new-pkgs --no-install-recommends
cuda_packages > /tmp/cuda-after.lock
cmp /tmp/cuda-before.lock /tmp/cuda-after.lock
cp /tmp/cuda-after.lock /usr/local/share/image-studio/cuda-packages.lock
