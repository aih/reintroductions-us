#!/usr/bin/env bash
# Creates or updates the function reintro-pdf from an image already in ECR,
# then makes sure the queue triggers it. Run by .github/workflows/pdf-lambda.yml:
#
#   bash deploy/lambda-deploy.sh <image-uri>
#
# Needs REINTRO_PREGEN_BUCKET and REINTRO_PREGEN_QUEUE_ARN, and the role
# deploy/aws-setup.sh creates. The function: arm64, 4096 MB, 900 s; the
# trigger: one message per invocation, at most 10 running at once.
set -euo pipefail

IMAGE="${1:?usage: lambda-deploy.sh <image-uri>}"
NAME="reintro-pdf"
BUCKET="${REINTRO_PREGEN_BUCKET:?}"
QUEUE_ARN="${REINTRO_PREGEN_QUEUE_ARN:?}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/reintro-pdf-lambda"
MEMORY=4096
TIMEOUT=900
CONCURRENCY=10

if aws lambda get-function --function-name "$NAME" >/dev/null 2>&1; then
    echo "updating $NAME to $IMAGE"
    aws lambda update-function-code --function-name "$NAME" --image-uri "$IMAGE" >/dev/null
    aws lambda wait function-updated-v2 --function-name "$NAME"
    aws lambda update-function-configuration --function-name "$NAME" \
        --memory-size "$MEMORY" --timeout "$TIMEOUT" \
        --environment "Variables={REINTRO_PREGEN_BUCKET=$BUCKET}" >/dev/null
    aws lambda wait function-updated-v2 --function-name "$NAME"
else
    echo "creating $NAME from $IMAGE"
    aws lambda create-function --function-name "$NAME" --package-type Image \
        --code "ImageUri=$IMAGE" --role "$ROLE_ARN" --architectures arm64 \
        --memory-size "$MEMORY" --timeout "$TIMEOUT" \
        --environment "Variables={REINTRO_PREGEN_BUCKET=$BUCKET}" >/dev/null
    aws lambda wait function-active-v2 --function-name "$NAME"
fi

uuid="$(aws lambda list-event-source-mappings --function-name "$NAME" --event-source-arn "$QUEUE_ARN" \
    --query 'EventSourceMappings[0].UUID' --output text)"
if [ -z "$uuid" ] || [ "$uuid" = "None" ]; then
    echo "creating the queue trigger"
    aws lambda create-event-source-mapping --function-name "$NAME" --event-source-arn "$QUEUE_ARN" \
        --batch-size 1 --function-response-types ReportBatchItemFailures \
        --scaling-config "MaximumConcurrency=$CONCURRENCY" >/dev/null
else
    aws lambda update-event-source-mapping --uuid "$uuid" --batch-size 1 \
        --function-response-types ReportBatchItemFailures \
        --scaling-config "MaximumConcurrency=$CONCURRENCY" >/dev/null
fi
aws lambda get-function --function-name "$NAME" \
    --query '{state: Configuration.State, image: Code.ImageUri, memory: Configuration.MemorySize}' --output table
