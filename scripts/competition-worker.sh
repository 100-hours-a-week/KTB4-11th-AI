competition_worker_project=""
competition_worker_defined=false
competition_worker_existed=false
competition_worker_running=false
competition_worker_recovery_needed=false

snapshot_competition_worker() {
  local services ids running container
  competition_worker_project="$(docker compose --env-file .env.next --env-file .image.env.next \
    -f compose.prod.yaml.next --profile competition config --format json --no-env-resolution \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')" || return $?
  if [[ -f compose.prod.yaml && -f .env && -f .image.env ]]; then
    services="$(docker compose --env-file .env --env-file .image.env \
      -f compose.prod.yaml --profile competition config --services --no-env-resolution)" || return $?
    if [[ "$services" == *competition-portfolio-builder* ]]; then
      competition_worker_defined=true
    fi
  fi
  ids="$(docker ps -a -q \
    --filter "label=com.docker.compose.project=$competition_worker_project" \
    --filter label=com.docker.compose.service=competition-portfolio-builder)" || return $?
  if [[ -n "$ids" ]]; then
    competition_worker_existed=true
    local -a containers=()
    while IFS= read -r container; do
      containers+=("$container")
    done <<< "$ids"
    running="$(docker inspect --format '{{.State.Running}}' "${containers[@]}")" || return $?
    if [[ "$running" == *true* ]]; then
      competition_worker_running=true
    fi
  fi
}

remove_competition_worker_containers() {
  local ids container
  local -a containers=()
  ids="$(docker ps -a -q \
    --filter "label=com.docker.compose.project=$competition_worker_project" \
    --filter label=com.docker.compose.service=competition-portfolio-builder)" || return $?
  if [[ -n "$ids" ]]; then
    while IFS= read -r container; do
      containers+=("$container")
    done <<< "$ids"
    docker stop --time 30 "${containers[@]}" || return $?
    docker rm "${containers[@]}" || return $?
  fi
}

deploy_competition_worker() {
  docker compose --env-file .env --env-file .image.env -f compose.prod.yaml \
    run --rm --no-deps -T --entrypoint python competition-portfolio-builder -c \
    'from portfolio_builder.competition.__main__ import Settings, IntakeSettings
from disclosure_retrieval.settings import Settings as DisclosureSettings
settings = Settings()
IntakeSettings()
if settings.disclosure_enabled:
    DisclosureSettings()' || return $?
  competition_worker_recovery_needed=true
  remove_competition_worker_containers || return $?
  docker compose --env-file .env --env-file .image.env -f compose.prod.yaml \
    --profile competition up -d --no-deps --force-recreate --wait --wait-timeout 60 \
    competition-portfolio-builder
}

restore_competition_worker() {
  if [[ "$competition_worker_recovery_needed" != true ]]; then
    return 0
  fi
  remove_competition_worker_containers || return $?
  if [[ "$competition_worker_defined" == true && "$competition_worker_existed" == true ]]; then
    if [[ "$competition_worker_running" == true ]]; then
      docker compose --env-file .env --env-file .image.env -f compose.prod.yaml \
        --profile competition up -d --no-deps --wait --wait-timeout 60 \
        competition-portfolio-builder || return $?
    else
      docker compose --env-file .env --env-file .image.env -f compose.prod.yaml \
        --profile competition up --no-start --no-deps competition-portfolio-builder || return $?
    fi
  fi
}
