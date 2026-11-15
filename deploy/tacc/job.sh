#!/usr/bin/env bash
set -eo pipefail
umask 077
cd "${TACC_WORKDIR:?Submit with tcloud submit}"
source /mnt/home/public/course-envs/CSIT6000V/project/activate.sh
set -u

gpu_list="$(nvidia-smi --query-gpu=index --format=csv,noheader | paste -sd, -)"
if [[ "$gpu_list" =~ ^[0-9]+,[0-9]+$ ]]; then
    export CUDA_VISIBLE_DEVICES="$gpu_list"
fi
printf 'CUDA_VISIBLE_DEVICES=%s\n' "${CUDA_VISIBLE_DEVICES:-unset}"
gpu_memory="$(nvidia-smi --query-gpu=index,memory.free --format=csv,noheader,nounits)"
printf '%s\n' "$gpu_memory"
IFS=, read -r first_gpu second_gpu <<< "${CUDA_VISIBLE_DEVICES:-}"
if [[ "$first_gpu" =~ ^[0-9]+$ && "$second_gpu" =~ ^[0-9]+$ ]]; then
    if ! awk -F, -v first="$first_gpu" -v second="$second_gpu" '
        $1 == first || $1 == second { if ($2 >= 13500) available++ }
        END { exit available == 2 ? 0 : 1 }
    ' <<< "$gpu_memory"; then
        echo "The allocated GPUs do not have enough free memory to start Qwen." >&2
        exit 1
    fi
fi
export QWEN_CACHE_HOME="$TACC_USERDIR/CSIT6000V-project-cache"
mkdir -p -m 700 "$QWEN_CACHE_HOME"

port=$((30000 + SLURM_JOB_ID % 20000))
export QWEN_BASE_URL="http://127.0.0.1:$port/v1"
serve="$QWEN_RUN_DIR/serve-cinecontext.sh"
sed -e '/^export CUDA_VISIBLE_DEVICES=/d' \
    -e "s/--host 0.0.0.0 --port 8000/--host 127.0.0.1 --port $port/" \
    -e 's/--safetensors-load-strategy prefetch/--safetensors-load-strategy lazy/' \
    -e 's/--gpu-memory-utilization 0.90/--gpu-memory-utilization 0.55/' \
    -e 's/--max-model-len 32768/--max-model-len 16384/' \
    -e 's/--max-num-seqs 4/--max-num-seqs 1 --enforce-eager/' \
    "$QWEN_PROJECT_ROOT/serve.sh" > "$serve"
bash "$serve" &
server_pid=$!

cleanup() {
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    /usr/bin/python3 -c 'import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$QWEN_RUN_DIR"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
/usr/bin/python3 -u worker.py "$server_pid"
