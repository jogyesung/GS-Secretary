# CIP 미팅노트 양식 상세 스펙

이 스펙은 세 개의 하우스 참조 문서에서 추출한 값이다. 임의로 바꾸지 않는다.

- **Project White 미팅노트** (경영진 미팅) — 문서 구조·본문 밀도의 기준
- **Project Union 미팅노트** (전문가 인터뷰) — Executive Summary 소제목, 제목 스타일, E.O.D, 페이지 푸터의 기준
- **CIP 요양시설 시장분석 보고서** — 흑백 톤앤매너, 표 스타일, 주석(출처·Note) 표기의 기준

`scripts/build_meeting_note.js`가 아래 스펙 전체를 코드로 강제하므로,
직접 docx를 조립하지 말고 반드시 빌더를 사용한다.

## 1. 페이지·타이포그래피

| 항목 | 값 | 출처 |
|---|---|---|
| 용지 | A4 (210×297mm) | White/Union |
| 여백 | 상하좌우 13mm | White/Union |
| 라틴 폰트 | Arial | White |
| 한글 폰트 | 맑은 고딕 | Union |
| 본문 크기 | 10pt | White |
| 제목·Executive Summary 헤딩 | 12pt bold | Union |
| 섹션 헤딩 (1., 1-1.) | 10pt bold (본문과 동일 크기) | White/Union |
| 행간 | 256 (auto, 약 1.07배) | White |
| 정렬 | 본문 불릿 양쪽 정렬, 헤딩 왼쪽 정렬 | Union/CIP |
| 한글 조판 | autoSpaceDE/DN·wordWrap 해제 (숫자·한글 사이 자동 여백 없음) | White |
| 색상 | 본문 검정(#000000)만. 강조는 색이 아니라 굵게+밑줄 | 흑백톤 (CIP) |

## 2. 문서 구조 (위에서 아래 순서)

```
[CENTROID 로고]                      ← 1.97in×0.56in, 1페이지 상단 (assets/centroid_logo.jpg)
Project {코드명} Meeting Note        ← 12pt bold + 하단 회색 구분선(sz6, #A0A0A0)
Subject: {주제}                      ← 10pt bold
Date: 2026-07-24 (금) 17:00~18:00   ← 10pt bold, 시간까지 알 수 있으면 포함
Attendee: {아군 참석자}  |  Interviewee : {상대방}   ← 10pt bold

Executive Summary                    ← 12pt bold
1. {소제목}                          ← 10pt bold  ★ 소제목 필수 (Union 방식)
- 핵심 요점
- 핵심 요점
2. {소제목}
- ...

1. {본문 대주제}                      ← 10pt bold, 자동 번호
- 사실 불릿
[표 N] {캡션}                        ← 9.5pt bold
{표}                                 ← 9pt, 머리행 #F2F2F2 음영, 테두리 #D9D9D9
Note: ...  Source: ...               ← 7.5pt italic #999999 (해당 시)
{소제목 행}                           ← 10pt bold (번호 없는 미니 헤딩, 예: "해병대 2사단")
1-1. {소주제}                         ← 10pt bold, 자동 번호
- 사실 불릿
    · 하위 불릿                       ← 들여쓰기 360twip
...

******...E.O.D...******              ← italic, 중앙 정렬 (Union)
```

- 푸터: 하단 중앙 `현재쪽/전체쪽` (예: 1/6)
- 각주: 페이지 하단, 8pt, #595959

## 3. 불릿·강조 규칙

- 1단계 불릿 접두사는 `- ` (하이픈+공백), 2단계는 `· ` — 빌더가 자동으로 붙이므로 JSON 텍스트에는 넣지 않는다
- 불릿 안에서 열거가 필요하면 `① ② ③` 원문자 사용 (White/Union 공통 관행)
- 핵심 결론·리스크 등 문서에서 가장 중요한 문장만 굵게+밑줄(`emphasis`) — 한 문서에 몇 개 수준으로 아껴 쓴다. 남발하면 강조가 죽는다
- 발언 주체 귀속 표현: "회사는 ~라고 설명", "대표이사는 ~로 판단", "투자자 측은 ~ 우려를 제기" (White 관행)
- 보충·당부 사항은 `(참고) ...` 불릿으로 (White 관행)

## 4. 표 사용 기준

파이프라인, 지표 비교, 시계열 등 **구조화된 수치가 3행 이상** 나열될 때 표로 만든다.
그 미만이면 불릿이 낫다. 표에는:

- 캡션: `[표 N] 제목` — 번호는 빌더가 자동 부여
- 머리행: #F2F2F2 음영 + bold + 가운데 정렬 (빌더 자동)
- 수치 출처가 회사 구두 언급인지, 자료 기반인지 `Source:`에 명시 (CIP 관행)
- 표 안 수치에 대한 유보 사항은 `Note:`에 명시

## 5. 주석(각주) 사용 기준 — CIP 관행의 미팅노트 적용

각주(`footnote`)를 다는 경우:

1. **녹취 불명확** — 발음이 뭉개져 두 가지 이상으로 들리는 고유명사·수치.
   본문에는 가장 그럴듯한 표기를 쓰되 각주로 `녹취 불명확 — {대안} 재확인 필요`
2. **재확인 필요 수치** — 발화 간 상충하거나 자료 대조가 필요한 숫자
3. **용어·맥락 설명** — 독자가 모를 수 있는 전문용어, 언급된 인물·회사의 배경 등
   녹취에서 직접 확인 가능한 사실만. 외부 지식으로 보태는 설명은 그 출처가 명확할 때만

표 아래 `Source:`/`Note:` 행은 표 데이터의 출처·유보 표기에 사용한다 (각주와 구분).

## 6. 빌더 입력 JSON 스키마

```jsonc
{
  "title": "Project Union Meeting Note",   // "Project {코드명} Meeting Note"
  "subject": "캐피탈(산은캐피탈) 출신 전문가 인터뷰 : 캐피탈 업계 전문가",
  "date": "2026-07-24 (금) 17:00~18:00",   // 시간 미상이면 날짜만
  "attendee": "김진만 상무, 조계승 이사",     // 아군(CIP) 참석자
  "interviewee": "산은캐피탈 출신 전문가",    // 선택. 인터뷰·상대방이 있으면
  "logo": true,                             // 선택. false면 로고 생략
  "logoPath": "...",                        // 선택. 로고 파일 경로 교체

  "executiveSummary": [                     // ★ 필수. 소제목 3~7개, 자동 번호
    { "heading": "소제목", "bullets": [ /* <item> 배열 */ ] }
  ],

  "sections": [                             // 자동 번호 1., 2., ... / 1-1., 1-2., ...
    {
      "heading": "대주제",
      "content": [ /* <item> 배열 */ ],
      "subsections": [
        { "heading": "소주제", "content": [ /* <item> 배열 */ ] }
      ]
    }
  ]
}
```

`<item>` 은 다음 중 하나:

| 형태 | 렌더링 |
|---|---|
| `"문자열"` | `- ` 불릿 (양쪽 정렬) |
| `{ "text": "...", "level": 2 }` | `· ` 하위 불릿 (들여쓰기) |
| `{ "text": "...", "emphasis": true }` | 굵게+밑줄 강조 불릿 |
| `{ "text": "...", "footnote": "..." }` | 불릿 끝에 각주 마커 (`footnotes: [..]`로 복수 가능) |
| `{ "heading": "..." }` | 번호 없는 굵은 미니 헤딩 |
| `{ "table": { ... } }` | 표 블록 (아래 참조) |

`table` 오브젝트:

```jsonc
{
  "caption": "주요 영업 Pipeline",      // 선택. "[표 N]" 접두사는 자동
  "columns": ["구분", "고객사", ...],
  "widths": [1, 1.4, 1, ...],           // 선택. 열 너비 비율 (기본 균등)
  "rows": [["...", "...", ...], ...],
  "source": "회사 제시 자료",            // 선택 → "Source: ..." 회색 행
  "note": "회사 자체 평가 기준"          // 선택 → "Note: ..." 회색 행
}
```

## 7. 실행·검증

```bash
# docx 패키지가 없으면: npm install docx  (스크래치 디렉토리에서)
node skills/meeting-summary/scripts/build_meeting_note.js note.json "Project_X_미팅노트_260727.docx"

# 렌더링 검증 (docx 스킬 동일 패턴)
soffice --headless --convert-to pdf --outdir . output.docx
pdftoppm -jpeg -r 90 output.pdf page   # page-*.jpg 를 열어 눈으로 확인
```

확인 항목: 로고·제목 구분선 표시 / Executive Summary 소제목 번호 / 표 잘림 없음 /
각주 마커·본문 대응 / E.O.D / 페이지 푸터 / 색상은 흑백·회색 계열만.

## 8. 파일명 규칙

- 국문: `Project_{코드명}_{주제 또는 차수}_{YYMMDD}.docx` (예: `Project_Union_산은캐피탈 인터뷰_260727.docx`)
- 영문판: 동일 이름 + `_ENG` 접미사
- 차수가 있는 정기 미팅이면 주제 자리에 `미팅노트 N차`
