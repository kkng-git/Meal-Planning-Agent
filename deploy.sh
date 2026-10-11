#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f .env ]]; then
    echo "Missing .env. Copy .env.example to .env and add your API keys." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to your Google Cloud project ID.}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY is missing from .env.}"
: "${TAVILY_API_KEY:?TAVILY_API_KEY is missing from .env.}"

REGION="${GCP_REGION:-us-west1}"
SERVICE_NAME="${CLOUD_RUN_SERVICE:-meal-planning-agent}"
REPOSITORY="${ARTIFACT_REPOSITORY:-meal-planning-agent}"
IMAGE_NAME="${CONTAINER_IMAGE_NAME:-meal-planning-agent}"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-5-mini}"
OPENAI_REASONING_EFFORT="${OPENAI_REASONING_EFFORT:-minimal}"
RUNTIME_SERVICE_ACCOUNT="${RUNTIME_SERVICE_ACCOUNT:-meal-planning-agent}"
RUNTIME_SERVICE_ACCOUNT_EMAIL="${RUNTIME_SERVICE_ACCOUNT}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
IMAGE_URL="${REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${REPOSITORY}/${IMAGE_NAME}:latest"

if [[ -z "$(gcloud auth list --filter=status:ACTIVE --format='value(account)')" ]]; then
    echo "No active Google Cloud login. Run: gcloud init" >&2
    exit 1
fi

gcloud config set project "$GCP_PROJECT_ID"

gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com

if ! gcloud artifacts repositories describe "$REPOSITORY" \
    --location "$REGION" >/dev/null 2>&1; then
    gcloud artifacts repositories create "$REPOSITORY" \
        --repository-format docker \
        --location "$REGION" \
        --description "Meal Planning Agent container images"
fi

if ! gcloud iam service-accounts describe \
    "$RUNTIME_SERVICE_ACCOUNT_EMAIL" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$RUNTIME_SERVICE_ACCOUNT" \
        --display-name "Meal Planning Agent Cloud Run runtime"
fi

upsert_secret() {
    local secret_name="$1"
    local secret_value="$2"

    if gcloud secrets describe "$secret_name" >/dev/null 2>&1; then
        printf '%s' "$secret_value" \
            | gcloud secrets versions add "$secret_name" --data-file=-
    else
        printf '%s' "$secret_value" \
            | gcloud secrets create "$secret_name" \
                --replication-policy automatic \
                --data-file=-
    fi

    gcloud secrets add-iam-policy-binding "$secret_name" \
        --member "serviceAccount:${RUNTIME_SERVICE_ACCOUNT_EMAIL}" \
        --role roles/secretmanager.secretAccessor >/dev/null
}

upsert_secret "meal-agent-openai-api-key" "$OPENAI_API_KEY"
upsert_secret "meal-agent-tavily-api-key" "$TAVILY_API_KEY"

gcloud builds submit \
    --region "$REGION" \
    --tag "$IMAGE_URL" \
    .

gcloud run deploy "$SERVICE_NAME" \
    --image "$IMAGE_URL" \
    --platform managed \
    --region "$REGION" \
    --allow-unauthenticated \
    --service-account "$RUNTIME_SERVICE_ACCOUNT_EMAIL" \
    --memory 512Mi \
    --min-instances 0 \
    --max-instances 1 \
    --concurrency 8 \
    --timeout 300 \
    --set-env-vars "LLM_PROVIDER=openai,OPENAI_MODEL=${OPENAI_MODEL},OPENAI_REASONING_EFFORT=${OPENAI_REASONING_EFFORT},RECIPE_DATA_DIR=/app/data" \
    --set-secrets "OPENAI_API_KEY=meal-agent-openai-api-key:latest,TAVILY_API_KEY=meal-agent-tavily-api-key:latest"

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" \
    --region "$REGION" \
    --format='value(status.url)')"

echo
echo "Deployment complete: $SERVICE_URL"
echo "Health check: curl -sS \"$SERVICE_URL/health\""
