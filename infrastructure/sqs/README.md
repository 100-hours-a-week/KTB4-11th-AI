# 계정·실패 큐 배포

`rebalancer-queues.yaml`은 #244의 계정·실패 FIFO 큐와 각각의 FIFO DLQ를 정의합니다. 기존 Report/Order 큐와 IAM Role은 재생성하지 않습니다. 모든 큐는 SSE-SQS, HTTPS 사용을 설정하고 삭제/교체 시 Retain합니다. URL은 계정 번호를 하드코딩하지 않고 stack output으로 얻습니다. 생성 전 같은 이름의 기존 큐가 있으면 해당 인프라 관리 방식에 설정을 반영하거나 리소스를 import하고, 중복 생성하지 않습니다.

| 설정 | 기본값 |
|---|---|
| 리전 | 배포 명령의 `ap-northeast-2` |
| Account 큐 | `stockspoon-v2-${Environment}-account-snapshot.fifo` |
| 일반 실패 알림 큐 | `stockspoon-v2-${Environment}-account-failure.fifo` |
| DLQ | 각 큐 이름의 `.fifo` 앞에 `-dlq` |
| long polling / visibility | 20초 / 300초 |
| 원본 / DLQ 보존 | 4일 / 14일 |
| maxReceiveCount | 5회 |
| source backlog / oldest age 알람 | 100개 / 900초 이상, 3분 지속 |
| DLQ 알람 | visible 1개 이상, 1분 |

`VisibilitySeconds`는 계정 메시지 검증·DB 커밋·삭제 시간을 넘어야 하며 drain 예산도 고려합니다. Report의 리포트 생성 시간과 visibility 연장은 별도 서비스 소관입니다. DLQ는 대응하는 원본 큐 ARN만 redrive 대상으로 허용합니다. FIFO 원본 ARN은 이름으로 계산해 CloudFormation 순환 참조를 피합니다. 알람은 SNS 토픽 ARN을 전달하면 알림을 발행하며 생략 시 CloudWatch 상태만 생성합니다. 운영 전에 기존 SNS 구독까지 연결합니다.

## 검증과 배포

권한이 있는 인프라 담당자가 EC2 role 또는 AWS SDK 기본 체인을 사용해 실행합니다. Access Key/Secret Key는 저장하지 않습니다. 이 PR에서는 실제 stack을 배포하지 않습니다.

```bash
uvx cfn-lint infrastructure/sqs/rebalancer-queues.yaml
aws cloudformation validate-template --template-body file://infrastructure/sqs/rebalancer-queues.yaml --region ap-northeast-2
aws cloudformation deploy --template-file infrastructure/sqs/rebalancer-queues.yaml --stack-name stockspoon-v2-dev-rebalancer-queues --parameter-overrides Environment=dev AlarmTopicArn=<existing-sns-topic-arn> --region ap-northeast-2
aws cloudformation describe-stacks --stack-name stockspoon-v2-dev-rebalancer-queues --query 'Stacks[0].Outputs' --region ap-northeast-2
```

stack output의 `AccountQueueUrl`, `FailureQueueUrl`을 다른 서비스와 같은 배포 `.env`의 `PORTFOLIO_REBALANCER_ACCOUNT_QUEUE_URL`, `PORTFOLIO_REBALANCER_FAILURE_QUEUE_URL`에 반영합니다. `ORDER_QUEUE_URL`은 기존 값을 유지합니다. `AWS_DEFAULT_REGION=ap-northeast-2`를 설정합니다. 큐 URL 출력은 두 Compose 파일이 Settings로 전달합니다.

기존 AI role에는 Account ARN의 `sqs:ReceiveMessage`, `sqs:DeleteMessage`, Order/Failure ARN의 `sqs:SendMessage`만 필요합니다. 큐 URL이 주어지므로 GetQueueUrl은 필요하지 않습니다. Backend role은 Account SendMessage, Order/Failure ReceiveMessage/DeleteMessage가 필요합니다. 애플리케이션 role에는 DLQ 발행·소비 권한을 부여하지 않습니다. 실제 role 연결과 정책 변경은 해당 인프라 관리 저장소에서 적용합니다.

배포 전 `KTB_POSTGRES_DSN`을 설정하고 `uv run alembic upgrade head`를 실행해 0011 마이그레이션까지 적용합니다. 애플리케이션은 부팅 시 테이블을 만들지 않습니다. 운영에서는 `.env` 반영 뒤 기존 단일 호스트 스케줄러와 flock을 유지합니다.

## dev 실패·격리·복구 확인

1. 테스트 환경에서만 Account 큐의 `accounts` 그룹에 잘못된 payload와 뒤따르는 정상 전체 스냅샷을 발행합니다. eventId와 DeduplicationId는 각각 고유하게 지정합니다.
2. AI 실행 로그의 반려 원본 IDs와 Failure 큐의 알림을 확인합니다. 원본 메시지가 삭제되지 않고 동일 FIFO 배치의 뒤쪽이 처리되지 않아야 합니다.
3. visibility가 만료된 뒤 AI를 재실행해 `MaxReceiveCount`만큼 재수신을 유도합니다. 스케줄러는 장중 시간당 동작하고 오류 재시작은 5분 간격이므로 redrive는 즉시 일어나지 않습니다. 테스트 모드 사용 시 실제 주문 큐 대신 테스트용 주문 큐를 지정합니다.
4. 실패 메시지가 Account DLQ로 격리되고 DLQ 알람이 발생하는지 확인합니다. DLQ에 일반 소비자를 붙이거나 애플리케이션에서 직접 발행하지 않습니다.
5. 신선한 정상 전체 스냅샷을 새 eventId/occurredAt으로 발행하고 AI가 상태를 커밋·삭제하는지 확인합니다. 대기 중 정상 스냅샷도 900초 유효기간이 지나면 거래에 사용하지 않습니다.
6. 원인을 수정한 재처리 가능한 메시지만 운영자의 수동 redrive로 원본 큐에 되돌립니다. Redrive는 본문을 수정할 수 없으므로 잘못된/만료된 스냅샷을 그대로 redrive하지 않습니다. Backend에서 최신 전체 상태를 새 이벤트로 재발행합니다. 실제 삭제가 필요한 잘못된 DLQ 메시지는 원인 확인 뒤 별도 운영 승인으로 정리합니다.
7. 일반 Failure 큐도 Backend가 알림 처리에 실패하면 별도 Failure DLQ로 이동하는지 확인하고 backlog/age 경보가 정상화되는지 기록합니다.

#244 완료에는 실제 큐 생성, URL 반영, SNS 구독 및 위 dev 검증 결과가 필요합니다. 템플릿 정적 검증만으로 완료 처리하지 않습니다.

참고: [AWS CloudFormation SQS Queue](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-sqs-queue.html), [SQS CloudWatch metrics](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-available-cloudwatch-metrics.html), [DLQ redrive](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-configure-dead-letter-queue-redrive.html).
