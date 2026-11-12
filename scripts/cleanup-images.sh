#!/usr/bin/env bash
set -Eeuo pipefail
cd "${1:-$HOME/ai}"
trap 'echo "cleanup-images: failed at line $LINENO (status $?)" >&2' ERR

# CD와 같은 잠금을 사용해 배포 도중 이미지가 정리되지 않도록 보호
exec 9>>.deploy.lock
if ! flock -n 9; then
  echo 'cleanup-images: skipped; deployment or cleanup is active'
  exit 0
fi
# 파일 전송이 시작됐지만 배포 잠금은 아직 잡히지 않은 구간도 건너뜀 (잠금은 배포 스크립트에서 잡음)
if [[ -e .image.env.next || -e compose.prod.yaml.next || -e .schedule.next ]]; then
  echo 'cleanup-images: skipped; deployment is staged'
  exit 0
fi

# 조회 결과는 임시 파일에 작성, 성공·실패와 관계없이 종료 시 제거
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

# 현재&롤백 Compose의 모든 프로필에서 예약 배치를 포함한 이미지 목록 조회
# (첫 배포에는 롤백 파일이 없을 수 있지만 일부만 존재 시 안전하게 중단)
for suffix in '' '.rollback'; do
  if [[ "$suffix" == '.rollback' && ! -e .env.rollback &&
        ! -e .image.env.rollback && ! -e compose.prod.yaml.rollback ]]; then
    continue
  fi
  for file in .env .image.env compose.prod.yaml; do
    [[ -f "$file$suffix" ]] || { echo "cleanup-images: missing $file$suffix" >&2; exit 1; }
  done
  docker compose --profile '*' \
    --env-file ".env$suffix" --env-file ".image.env$suffix" \
    -f "compose.prod.yaml$suffix" config --images >> "$work_dir/refs"
done
[[ -s "$work_dir/refs" ]] || { echo 'cleanup-images: no Compose images found' >&2; exit 1; }

# 태그·digest가 달라도 같은 이미지라면 하나의 로컬 ID로 합침
# (필요한 이미지가 로컬에 없을 경우 보존 컨테이너를 바꾸기 전에 중단)
while IFS= read -r image; do
  docker image inspect --format '{{.Id}}' "$image" >> "$work_dir/images"
done < "$work_dir/refs"
sort -u "$work_dir/images" -o "$work_dir/images"

# 이 배포 경로에서 만든 보존 컨테이너만 관리하도록 소유 라벨을 구분
owner=$(printf '%s' "$PWD" | sha256sum)
label="com.ktb.image-keep-owner=${owner:0:16}"
previous=$(docker container ls -aq --filter "label=$label")

# 컨테이너를 실행하지 않고 생성만 진행하여 필요한 이미지를 prune 대상에서 제외
# 새 보존 컨테이너가 모두 생성되기 전에는 이전 보존 컨테이너를 제거하지 않습니다.
while IFS= read -r image; do
  docker container create --pull never \
    --name "ktb-image-keep-${work_dir##*/}-${image#sha256:}" \
    --label "$label" --network none --entrypoint /bin/true "$image"
done < "$work_dir/images"

# 이전 보존 컨테이너만 제거, 실행 중이면 강제로 제거하지 않고 중단
while IFS= read -r container; do
  [[ -n "$container" ]] || continue
  docker container rm "$container"
done <<< "$previous"

# (이전까지는 점검 단계, 여기가 실제 정리 단계)
# 생성 후 7일이 지났고 앱 라벨이 있으며, 어떤 컨테이너도 참조하지 않는 이미지만 정리
docker image prune -a -f \
  --filter 'until=168h' \
  --filter 'label=com.ktb.app=ai'
