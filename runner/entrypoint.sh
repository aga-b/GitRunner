#!/usr/bin/env bash
set -euo pipefail
jit="${RUNNER_JIT_CONFIG:?Missing one-use GitHub JIT configuration}"
unset RUNNER_JIT_CONFIG
# Do not echo the encoded config or enable shell tracing.
exec ./run.sh --jitconfig "$jit"
