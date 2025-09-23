#!/usr/bin/env bash
# sync_env_to_cloud_run.sh
# Usage:
#   PROJECT_ID=schoolbrief-prod SERVICE=schoolbrief REGION=us-west1 \
#   ./sync_env_to_cloud_run.sh ./.env
#
# Notes:
# - Sensitive keys are stored in Secret Manager; non-sensitive are plain envs.
# - We never echo values to the console.
# - If any value contains commas, we use a custom delimiter for gcloud flags.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-schoolbrief-prod}"
SERVICE="${SERVICE:-schoolbrief}"
REGION="${REGION:-us-west1}"
ENV_FILE="${1:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ ENV file not found: $ENV_FILE" >&2
  exit 1
fi

echo "🔧 Project: $PROJECT_ID  Service: $SERVICE  Region: $REGION"
gcloud config set project "$PROJECT_ID" >/dev/null

# Find the service account used by Cloud Run (may be empty if service doesn't exist yet)
SA="$(gcloud run services describe "$SERVICE" --region "$REGION" \
      --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true)"

# Heuristic: keys matching these patterns are treated as secrets
is_secret_key() {
  local k="$1"
  [[ "$k" =~ (SECRET|PASSWORD|TOKEN|API_KEY|WEBHOOK|CLIENT_SECRET|SESSION_SECRET|APP_SECRET) ]]
}

# Arrays to accumulate mappings
declare -a UPDATE_SECRETS=()
declare -a PLAIN_ENV=()

# Normalize line endings and parse KEY=VALUE lines, ignoring comments/blank lines
while IFS= read -r line; do
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  # Allow KEY="VALUE with = signs"
  if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
    KEY="${BASH_REMATCH[1]}"
    RAW_VAL="${BASH_REMATCH[2]}"
    # Strip optional surrounding quotes without expanding escapes
    if [[ "$RAW_VAL" =~ ^\"(.*)\"$ ]]; then
      VALUE="${BASH_REMATCH[1]}"
    elif [[ "$RAW_VAL" =~ ^\'(.*)\'$ ]]; then
      VALUE="${BASH_REMATCH[1]}"
    else
      VALUE="$RAW_VAL"
    fi

    if is_secret_key "$KEY"; then
      # Create secret if it doesn't exist; then add a version
      if ! gcloud secrets describe "$KEY" --project "$PROJECT_ID" >/dev/null 2>&1; then
        echo "➕ Creating secret $KEY"
        gcloud secrets create "$KEY" --project "$PROJECT_ID" >/dev/null
      else
        echo "🆕 Adding new version to secret $KEY"
      fi
      printf %s "$VALUE" | gcloud secrets versions add "$KEY" \
        --project "$PROJECT_ID" --data-file=- >/dev/null

      # Grant accessor to the Cloud Run service account (if known)
      if [[ -n "$SA" ]]; then
        gcloud secrets add-iam-policy-binding "$KEY" --project "$PROJECT_ID" \
          --member="serviceAccount:${SA}" \
          --role="roles/secretmanager.secretAccessor" >/dev/null || true
      fi

      UPDATE_SECRETS+=("${KEY}=${KEY}:latest")
    else
      # Collect plain env var (we won't print values)
      PLAIN_ENV+=("${KEY}=${VALUE}")
    fi
  fi
done < <(sed -e 's/\r$//' "$ENV_FILE")

# Build args with a safe custom delimiter (avoid commas in values)
DELIM="^~^"
SECRET_ARG=()
ENV_ARG=()

if ((${#UPDATE_SECRETS[@]})); then
  SECRET_ARG=( --update-secrets "${DELIM}$(IFS='~'; echo "${UPDATE_SECRETS[*]}")" )
fi
if ((${#PLAIN_ENV[@]})); then
  ENV_ARG=( --update-env-vars "${DELIM}$(IFS='~'; echo "${PLAIN_ENV[*]}")" )
fi

echo "🚀 Updating Cloud Run service env (this creates a new revision)..."
gcloud run services update "$SERVICE" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  "${SECRET_ARG[@]}" \
  "${ENV_ARG[@]}"

echo "✅ Done. New revision rolling out."
