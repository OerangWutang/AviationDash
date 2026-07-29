#!/usr/bin/env bash
# Shared fail-closed Compose selection for production operational helpers.

atlas_argus_select_compose_file() {
  if [[ "${ATLAS_ARGUS_ENV:-dev}" != "production" ]]; then
    return 0
  fi

  local selected="${ATLAS_ARGUS_PRODUCTION_COMPOSE_FILE:-docker-compose.production.example.yml}"
  if [[ "$(basename -- "$selected")" == "docker-compose.yml" ]]; then
    printf 'Refusing to use the development Compose file for production operations.\n' >&2
    return 2
  fi
  if [[ ! -f "$selected" || ! -r "$selected" ]]; then
    printf 'Production Compose file is not a readable file: %s\n' "$selected" >&2
    return 2
  fi
  if [[ -f docker-compose.yml ]] && \
    [[ "$(readlink -f -- "$selected")" == "$(readlink -f -- docker-compose.yml)" ]]; then
    printf 'Refusing a production Compose path that resolves to docker-compose.yml.\n' >&2
    return 2
  fi

  export ATLAS_ARGUS_PRODUCTION_COMPOSE_FILE="$selected"
  export COMPOSE_FILE="$selected"
}
