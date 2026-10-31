#!/usr/bin/env bash
# One-time AWS setup for the PDF pre-generation (docs/deploy.md), by an
# administrator, from AWS CloudShell in us-east-1 or any shell with admin
# credentials:
#
#   bash deploy/aws-setup.sh
#
# Creates, each only when absent:
#
#   - S3 bucket ${BUCKET}: public access blocked, `in/` expires after 14 days
#   - SQS queue reintro-pdf (visibility 960 s) and its dead-letter queue
#     reintro-pdf-dlq (a message moves there after 2 failed receipts)
#   - ECR repository reintro-pdf (the last 5 images kept), with a policy that
#     lets Lambda pull from it
#   - CloudWatch log group /aws/lambda/reintro-pdf, 30 days
#   - role reintro-pdf-lambda, the function's: read `in/`, write `pdf/`,
#     receive from the queue, write its logs
#   - inline policy reintro-pdf on the instance role uscode-site: write `in/`,
#     read and list the bucket, send to the queue
#   - OIDC role reintro-github-deploy for aih/reintroductions-us: push to the
#     ECR repository, create and update the function reintro-pdf and its queue
#     trigger, pass the function's role, and run AWS-RunShellScript over SSM
#     on the instance tagged Name=uscode-site (.github/workflows/deploy.yml)
#
# The function itself is created by the first run of
# .github/workflows/pdf-lambda.yml, once the image exists. Re-running this
# script updates the policies in place.
set -euo pipefail

ACCOUNT_ID="${ACCOUNT_ID:-739065237548}"
REGION="${AWS_REGION:-us-east-1}"
BUCKET="${BUCKET:-reintro-linkedlegislation}"
GITHUB_REPO="aih/reintroductions-us"
NAME="reintro-pdf"
LAMBDA_ROLE="reintro-pdf-lambda"
SITE_ROLE="uscode-site"
GITHUB_ROLE="reintro-github-deploy"
OIDC_URL="token.actions.githubusercontent.com"
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_URL}"
FUNCTION_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${NAME}"
export AWS_DEFAULT_REGION="$REGION"

caller="$(aws sts get-caller-identity --query Account --output text)"
if [ "$caller" != "$ACCOUNT_ID" ]; then
    echo "these credentials are for account $caller, not $ACCOUNT_ID" >&2
    exit 1
fi
echo "=== reintro PDF pre-generation setup (account $ACCOUNT_ID, $REGION) ==="

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

role_exists() { aws iam get-role --role-name "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------ bucket
echo "--- bucket $BUCKET"
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "    exists"
else
    if [ "$REGION" = "us-east-1" ]; then
        aws s3api create-bucket --bucket "$BUCKET" >/dev/null
    else
        aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
    fi
    echo "    created"
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration '{
  "Rules": [{"ID": "in-expires", "Status": "Enabled", "Filter": {"Prefix": "in/"},
             "Expiration": {"Days": 14}}]}'

# ------------------------------------------------------------------ queues
echo "--- queues $NAME, $NAME-dlq"
DLQ_URL="$(aws sqs create-queue --queue-name "$NAME-dlq" \
    --attributes MessageRetentionPeriod=1209600 --query QueueUrl --output text)"
DLQ_ARN="$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn \
    --query Attributes.QueueArn --output text)"
cat > "$TMP/queue.json" <<EOF
{"VisibilityTimeout": "960", "MessageRetentionPeriod": "345600",
 "RedrivePolicy": "{\"deadLetterTargetArn\":\"$DLQ_ARN\",\"maxReceiveCount\":\"2\"}"}
EOF
if QUEUE_URL="$(aws sqs get-queue-url --queue-name "$NAME" --query QueueUrl --output text 2>/dev/null)"; then
    aws sqs set-queue-attributes --queue-url "$QUEUE_URL" --attributes "file://$TMP/queue.json"
else
    QUEUE_URL="$(aws sqs create-queue --queue-name "$NAME" --attributes "file://$TMP/queue.json" \
        --query QueueUrl --output text)"
fi
QUEUE_ARN="$(aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names QueueArn \
    --query Attributes.QueueArn --output text)"
echo "    $QUEUE_URL"

# --------------------------------------------------------------------- ECR
echo "--- ECR repository $NAME"
aws ecr describe-repositories --repository-names "$NAME" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$NAME" >/dev/null
aws ecr put-lifecycle-policy --repository-name "$NAME" --lifecycle-policy-text '{
  "rules": [{"rulePriority": 1, "description": "keep the last 5 images",
             "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 5},
             "action": {"type": "expire"}}]}' >/dev/null
aws ecr set-repository-policy --repository-name "$NAME" --policy-text "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{\"Sid\": \"LambdaPull\", \"Effect\": \"Allow\",
    \"Principal\": {\"Service\": \"lambda.amazonaws.com\"},
    \"Action\": [\"ecr:BatchGetImage\", \"ecr:GetDownloadUrlForLayer\"],
    \"Condition\": {\"StringLike\": {\"aws:sourceArn\": \"$FUNCTION_ARN\"}}}]}" >/dev/null

# --------------------------------------------------------------------- logs
echo "--- log group /aws/lambda/$NAME"
aws logs create-log-group --log-group-name "/aws/lambda/$NAME" 2>/dev/null || true
aws logs put-retention-policy --log-group-name "/aws/lambda/$NAME" --retention-in-days 30

# ----------------------------------------------------- the function's role
echo "--- role $LAMBDA_ROLE"
if ! role_exists "$LAMBDA_ROLE"; then
    aws iam create-role --role-name "$LAMBDA_ROLE" --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                     "Action": "sts:AssumeRole"}]}' >/dev/null
    echo "    created"
fi
cat > "$TMP/lambda.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "ReadXml", "Effect": "Allow", "Action": "s3:GetObject",
     "Resource": "arn:aws:s3:::${BUCKET}/in/*"},
    {"Sid": "WritePdf", "Effect": "Allow", "Action": "s3:PutObject",
     "Resource": "arn:aws:s3:::${BUCKET}/pdf/*"},
    {"Sid": "Queue", "Effect": "Allow",
     "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"],
     "Resource": "${QUEUE_ARN}"},
    {"Sid": "Logs", "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
     "Resource": "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/lambda/${NAME}:*"}
  ]
}
EOF
aws iam put-role-policy --role-name "$LAMBDA_ROLE" --policy-name "$LAMBDA_ROLE" \
    --policy-document "file://$TMP/lambda.json"

# ---------------------------------------------------- the instance's grant
echo "--- inline policy $NAME on role $SITE_ROLE"
if ! role_exists "$SITE_ROLE"; then
    echo "role $SITE_ROLE does not exist" >&2
    exit 1
fi
cat > "$TMP/site.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "WriteXml", "Effect": "Allow", "Action": "s3:PutObject",
     "Resource": "arn:aws:s3:::${BUCKET}/in/*"},
    {"Sid": "ReadPdf", "Effect": "Allow", "Action": "s3:GetObject",
     "Resource": "arn:aws:s3:::${BUCKET}/pdf/*"},
    {"Sid": "List", "Effect": "Allow", "Action": "s3:ListBucket",
     "Resource": "arn:aws:s3:::${BUCKET}"},
    {"Sid": "Send", "Effect": "Allow", "Action": ["sqs:SendMessage", "sqs:GetQueueAttributes"],
     "Resource": "${QUEUE_ARN}"}
  ]
}
EOF
aws iam put-role-policy --role-name "$SITE_ROLE" --policy-name "$NAME" --policy-document "file://$TMP/site.json"

# ------------------------------------------------------- the deploy role
echo "--- role $GITHUB_ROLE"
if [ -z "$(aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[?contains(Arn, '${OIDC_URL}')]" --output text)" ]; then
    echo "no OIDC provider for $OIDC_URL (the US Code site's deploy/admin-grant.sh creates it)" >&2
    exit 1
fi
cat > "$TMP/trust.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "${OIDC_ARN}"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                       "token.actions.githubusercontent.com:repository": "${GITHUB_REPO}"},
      "StringLike": {"token.actions.githubusercontent.com:sub": [
        "repo:${GITHUB_REPO%%/*}@*/${GITHUB_REPO##*/}@*:ref:refs/heads/main",
        "repo:${GITHUB_REPO}:ref:refs/heads/main"]}
    }
  }]
}
EOF
if role_exists "$GITHUB_ROLE"; then
    aws iam update-assume-role-policy --role-name "$GITHUB_ROLE" --policy-document "file://$TMP/trust.json"
else
    aws iam create-role --role-name "$GITHUB_ROLE" --assume-role-policy-document "file://$TMP/trust.json" >/dev/null
    echo "    created"
fi
cat > "$TMP/deploy.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "EcrAuth", "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
    {"Sid": "EcrPush", "Effect": "Allow",
     "Action": ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
                "ecr:PutImage", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload",
                "ecr:GetRepositoryPolicy", "ecr:SetRepositoryPolicy"],
     "Resource": "arn:aws:ecr:${REGION}:${ACCOUNT_ID}:repository/${NAME}"},
    {"Sid": "Function", "Effect": "Allow",
     "Action": ["lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:CreateFunction",
                "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration"],
     "Resource": "${FUNCTION_ARN}"},
    {"Sid": "Trigger", "Effect": "Allow",
     "Action": ["lambda:CreateEventSourceMapping", "lambda:UpdateEventSourceMapping", "lambda:GetEventSourceMapping"],
     "Resource": "*",
     "Condition": {"StringEquals": {"lambda:FunctionArn": "${FUNCTION_ARN}"}}},
    {"Sid": "ListTriggers", "Effect": "Allow", "Action": "lambda:ListEventSourceMappings", "Resource": "*"},
    {"Sid": "PassRole", "Effect": "Allow", "Action": "iam:PassRole",
     "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/${LAMBDA_ROLE}",
     "Condition": {"StringEquals": {"iam:PassedToService": "lambda.amazonaws.com"}}},
    {"Sid": "SsmSendCommandBox", "Effect": "Allow", "Action": "ssm:SendCommand",
     "Resource": "arn:aws:ec2:${REGION}:${ACCOUNT_ID}:instance/*",
     "Condition": {"StringEquals": {"ssm:resourceTag/Name": "uscode-site"}}},
    {"Sid": "SsmSendCommandDocument", "Effect": "Allow", "Action": "ssm:SendCommand",
     "Resource": "arn:aws:ssm:${REGION}::document/AWS-RunShellScript"},
    {"Sid": "SsmPollCommand", "Effect": "Allow", "Action": "ssm:GetCommandInvocation", "Resource": "*"},
    {"Sid": "FindBox", "Effect": "Allow", "Action": "ec2:DescribeInstances", "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name "$GITHUB_ROLE" --policy-name "$GITHUB_ROLE" \
    --policy-document "file://$TMP/deploy.json"

cat <<EOF

=== done ===

1. GitHub: Settings → Secrets and variables → Actions → Variables, in ${GITHUB_REPO}:
     AWS_DEPLOY_ROLE_ARN = arn:aws:iam::${ACCOUNT_ID}:role/${GITHUB_ROLE}
     REINTRO_PREGEN_BUCKET = ${BUCKET}
     REINTRO_PREGEN_QUEUE_ARN = ${QUEUE_ARN}
   then Actions → pdf-lambda → Run workflow.

2. The box's .env (~/reintroductions-us/.env):
     REINTRO_PREGEN_BUCKET=${BUCKET}
     REINTRO_PREGEN_QUEUE_URL=${QUEUE_URL}
EOF
