# Claude Skills

Claude(claude.ai / Claude Code)에서 쓰는 커스텀 스킬 모음. 스킬별로 한 폴더씩,
`SKILL.md`(트리거 조건 + 지침)와 부속 리소스(`references/`, `scripts/`, `assets/`)로 구성된다.

## 설치

- **claude.ai**: 스킬 폴더를 zip으로 묶어(`.skill`) 설정 → 기능(Capabilities) → 스킬에 업로드
- **Claude Code**: 폴더를 `~/.claude/skills/`에 복사

```bash
cd skills && zip -r meeting-summary.skill meeting-summary/
```

## 스킬 목록

| 스킬 | 설명 |
|---|---|
| [meeting-summary](meeting-summary/) | 회의·인터뷰 녹취록을 CIP 하우스 양식 미팅노트(.docx)로 정리. 소제목 있는 Executive Summary, 흑백 톤앤매너, 각주(재확인 표시), 영문 녹취 시 국/영문 이중 산출 |
