#!/usr/bin/env bash
# systemd OnSuccess=/OnFailure= 훅에서 호출되어 예약 작업 결과를
# AI_CWAgent/ScheduledJobFailure 지표로 발행합니다. (0: 성공, 1: 최종 실패)
set -Eeuo pipefail

readonly NAMESPACE="AI_CWAgent"
readonly METRIC_NAME="ScheduledJobFailure"
readonly IMDS="http://169.254.169.254/latest"
readonly AWS_CLI="${AWS_CLI:-/usr/local/bin/aws}"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <job-name> <0|1>" >&2
  exit 64
fi

job_name=$1
value=$2

[[ "$job_name" =~ ^[a-z0-9-]+$ ]] || { echo "invalid job name: $job_name" >&2; exit 64; }
[[ "$value" == 0 || "$value" == 1 ]] || { echo "invalid value: $value" >&2; exit 64; }
[[ -x "$AWS_CLI" ]] || { echo "AWS CLI not found at $AWS_CLI" >&2; exit 69; }

# IMDSv2(http_tokens=required)로 인스턴스 ID와 리전을 조회합니다.
imds_token="$(curl -fsS --max-time 2 -X PUT "$IMDS/api/token" \
  -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
imds_get() {
  curl -fsS --max-time 2 -H "X-aws-ec2-metadata-token: $imds_token" "$IMDS/meta-data/$1"
}
instance_id="$(imds_get instance-id)"
region="$(imds_get placement/region)"

"$AWS_CLI" cloudwatch put-metric-data \
  --region "$region" \
  --namespace "$NAMESPACE" \
  --metric-name "$METRIC_NAME" \
  --dimensions "InstanceId=$instance_id,JobName=$job_name" \
  --unit Count \
  --value "$value"

echo "published $NAMESPACE/$METRIC_NAME job=$job_name value=$value instance=$instance_id"
