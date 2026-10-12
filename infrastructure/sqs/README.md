# HTTP 조회 실패 알림 큐 배포

rebalancer-queues.yaml은 일반 Failure FIFO 큐와 FIFO DLQ, backlog/age/DLQ 알람을 정의합니다.
사용자 정보는 HTTP로 조회하므로 Account 큐를 만들지 않습니다. 기존 Report/Order 큐와 IAM Role은 재생성하지 않습니다.
큐는 SSE-SQS·HTTPS를 사용하고 삭제/교체 시 Retain합니다. 기존 동일 이름 큐가 있으면 중복 생성하지 말고 관리 방식에 맞게 import/수정합니다.

| 설정 | 기본값 |
|---|---|
| 리전 | ap-northeast-2 |
| 일반 실패 큐 | stockspoon-v2-${Environment}-account-failure.fifo |
| DLQ | stockspoon-v2-${Environment}-account-failure-dlq.fifo |
| long polling / visibility | 20초 / 300초 (Backend 소비자 기준) |
| 원본 / DLQ 보존 | 4일 / 14일 |
| maxReceiveCount | 5회 |
| backlog / age 알람 | 100개 / 900초 이상, 3분 |
| DLQ 알람 | visible 1개 이상, 1분 |

VisibilitySeconds는 Backend의 실패 알림 처리 시간을 넘도록 조정합니다.
SNS 토픽 ARN을 전달하면 알림을 발행하며 생략 시 CloudWatch 상태만 생성합니다.

## 배포

인프라 담당자가 EC2 IAM Role/기본 SDK 체인으로 실행합니다. 이 PR에서는 실제 배포하지 않습니다.

```bash
uvx cfn-lint==1.57.2 infrastructure/sqs/rebalancer-queues.yaml
aws cloudformation validate-template --template-body file://infrastructure/sqs/rebalancer-queues.yaml --region ap-northeast-2
aws cloudformation deploy --template-file infrastructure/sqs/rebalancer-queues.yaml --stack-name stockspoon-v2-dev-rebalancer-queues --parameter-overrides Environment=dev AlarmTopicArn=<existing-sns-topic-arn> --region ap-northeast-2
aws cloudformation describe-stacks --stack-name stockspoon-v2-dev-rebalancer-queues --query 'Stacks[0].Outputs' --region ap-northeast-2
```

FailureQueueUrl을 배포 .env의 PORTFOLIO_REBALANCER_FAILURE_QUEUE_URL에 설정합니다.
ORDER_QUEUE_URL과 HTTP 사용자 조회 Backend 설정을 유지하고 AWS_DEFAULT_REGION=ap-northeast-2를 사용합니다.
AI Role은 Order/Failure ARN에 sqs:SendMessage, Backend Role은 Order/Failure ARN에 sqs:ReceiveMessage/sqs:DeleteMessage가 필요합니다.
DLQ 일반 발행·소비 권한은 애플리케이션에 부여하지 않습니다. 관련 스냅샷 DB 마이그레이션은 없습니다.

## dev 검증

1. 실제 주문을 막은 테스트 환경에서 HTTP 조회 실패를 유도하고 account.snapshot.failed와 로그 추적 ID를 확인합니다.
2. Backend가 일반 실패 큐 메시지를 정상 처리한 뒤 삭제하는지 확인합니다.
3. 테스트 실패 메시지를 삭제하지 않을 때 maxReceiveCount 이후 Failure DLQ로 격리되는지 확인합니다.
4. backlog/age/DLQ 알람과 SNS 구독을 확인합니다. DLQ에 직접 발행하거나 일반 소비자를 붙이지 않습니다.
5. 원인 해결 후 운영자가 재처리 가능한 메시지만 수동 redrive합니다.

#244 완료에는 실제 큐·권한·URL·SNS 설정 및 dev 검증 결과가 필요합니다.
