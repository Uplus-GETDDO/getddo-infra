# Parameter Store 읽기 권한만 선언한다. 설정값·DB 비밀번호는 Terraform에 넣지 않는다.
# 실제 파라미터 값은 작업용 계정으로 별도 등록하고 서버 배포 절차에서 조회한다.
# 사용자 요청으로 동일 인라인 정책을 IAM에 직접 연결하며, 이 선언으로 후속 관리 내용을 남긴다.
resource "aws_iam_role_policy" "api_parameters" {
  name = "read-app-parameters"
  role = aws_iam_role.api.id

  # AWS 기본 alias/aws/ssm의 확인된 키 ARN과 앱 경로를 제한한 공통 정책 파일이다.
  # 계정·리전·암호화 키가 바뀌면 이 파일의 ARN도 재검토한다.
  policy = file("${path.module}/templates/iam/runtime/read-app-parameters.json")
}
