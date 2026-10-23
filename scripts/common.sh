#!/usr/bin/env bash
# Run-time environment resolver. Source only local, trusted .env files.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# Environment values provided by caller take precedence over .env defaults.
# Only trusted owners should edit .env because it is sourced as shell code.
declare -A _caller_env=()
while IFS='=' read -r _key _value; do
  case "$_key" in
    NVR_* ) _caller_env["$_key"]="$_value" ;;
  esac
done < <(env)
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi
for _key in "${!_caller_env[@]}"; do
  printf -v "$_key" '%s' "${_caller_env[$_key]}"
  export "$_key"
done
unset _caller_env _key _value
resolve_python() {
  local specified="${NVR_PYTHON:-}" env_name="${NVR_CONDA_ENV:-vision-nvr}"
  if [ -n "$specified" ]; then
    if [ ! -x "$specified" ]; then echo "指定的 Python 不存在: $specified" >&2; return 1; fi
    printf '%s\n' "$specified"; return
  fi
  if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    printf '%s\n' "$PROJECT_ROOT/.venv/bin/python"; return
  fi
  if command -v conda >/dev/null 2>&1; then
    local location
    location="$(conda env list | awk -v n="$env_name" '$1==n {print $NF;exit}')"
    if [ -n "$location" ] && [ -x "$location/bin/python" ]; then
      printf '%s\n' "$location/bin/python"; return
    fi
  fi
  echo '没有找到项目 Python 环境。请先运行 bash scripts/install.sh' >&2
  return 1
}
