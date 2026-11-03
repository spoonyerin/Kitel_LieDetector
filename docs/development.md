# TruthPulse 개발 문서

Claude Code는 작업 시작 전에 이 문서를 읽는다. 제품 요구사항은 `docs/PRD.md`에 있다.

## 0. 읽어야 할 문서

| 문서 | 내용 | 언제 읽나 |
|---|---|---|
| `docs/PRD.md` | 무엇을 만드는지, 기능 ID(F1~F14), 수락 기준 | 새 기능을 시작할 때 |
| `docs/development.md` (이 문서) | 구조, 규칙, 데이터 계약, 단계 | 항상 |
| `INTEGRATION.md` | 웹과 장치 연동 계약 제안 (기존 웹 프로토타입 문서) | 통신 코드를 만질 때 |
| `collector/record_session.py` 상단 설명 | 수집기 사용법과 저장 구조 | 수집기를 만질 때 |

## 1. 작업 규칙 (반드시 지킬 것)

1. **git은 사용자가 직접 한다.** `git commit`, `git push`, `git pull`, `git merge`, `git rebase`, `git reset`을 실행하지 않는다. 읽기 전용 명령(`git status`, `git diff`, `git log`)만 쓴다. 커밋이 필요한 시점이 오면 변경 요약만 알려 준다.
2. **커밋 메시지나 파일 어디에도 AI 공동작성 표기를 넣지 않는다.**
3. `data/real/` 아래는 읽기만 한다. 수정, 삭제, 덮어쓰기를 하지 않는다.
4. 모든 데이터, 기록, 결과에 `source` 값(`dummy` 또는 `hardware`)을 남긴다. `dummy` 데이터는 학습 코드가 절대 읽지 못하게 한다.
5. 정답 라벨(`ground_truth`, `condition`)은 판정과 특징 추출의 입력으로 쓰지 않는다. 테스트로 보장한다.
6. 판정기가 학습되지 않았으면(`trained=false`) 화면에 "모델 미학습(데모)"를 표시한다. 더미 결과로 정확도나 확률을 계산하거나 표시하지 않는다.
7. 큰 변경 전에는 무엇을 바꿀지 짧게 알리고, 기존 동작을 깨는 변경은 먼저 확인한다.
8. 새 기능에는 테스트를 같이 추가하고, 끝나면 테스트를 실행해 결과를 알려 준다.
9. 설명과 주석은 한국어로 쓴다.
10. 실측 데이터, 비밀 값, `.env`는 저장소에 올라가지 않게 `.gitignore`를 유지한다.
11. 문서와 코드가 달라지면 문서도 같이 고친다. 특히 11절의 이슈 목록과 12절의 단계 체크리스트를 갱신한다.

## 2. 폴더 구조

```
TruthPulse/
├─ CLAUDE.md              ← 로컬 전용 (깃에 올리지 않음). 이 문서를 불러오기만 한다.
├─ INTEGRATION.md
├─ docs/
│  ├─ PRD.md
│  └─ development.md
├─ collector/             ← 기존 LieDetector의 수집, 분석 코드
│  ├─ record_session.py
│  ├─ extract_features.py
│  ├─ calculate_bpm.py
│  ├─ recover_data.py
│  ├─ test_replay.py
│  └─ question_bank.csv
├─ firmware/              ← ESP32 펌웨어 (KITEL_MAIN_V5, 추가 필요)
├─ server/                ← 새로 만드는 Python 서버
│  ├─ app.py              ← FastAPI, WebSocket, 정적 파일 서빙
│  ├─ source/             ← 입력원: serial_source.py, replay_source.py, dummy_source.py
│  ├─ pipeline/           ← 품질 검사, 기준값, 특징 추출
│  ├─ judges/             ← base.py, dummy.py, rule.py, logreg.py
│  ├─ reasons.py          ← 기여도 → 이유 문장
│  ├─ storage.py          ← 기록 저장 (source 표시)
│  └─ models/             ← model.json (학습 결과)
├─ web/                   ← 기존 Truthpulse_Game 화면
│  └─ dist/
├─ training/              ← 학습 스크립트 (실측 데이터만 읽음)
├─ tests/
└─ data/                  ← 깃에 올리지 않음
   ├─ real/               ← 실측 (읽기 전용)
   └─ dummy/              ← 더미
```

## 3. 개발 환경과 실행 (Windows)

처음 한 번:

```
python -m venv .venv
.venv\Scripts\activate
pip install fastapi "uvicorn[standard]" pyserial numpy scipy pandas scikit-learn pytest
pip freeze > requirements.txt
```

서버 실행 (설계 결정: Python 서버가 `web/dist`를 정적 파일로 같이 서빙한다. 같은 포트라 CORS 설정이 필요 없다):

```
.venv\Scripts\activate
python -m uvicorn server.app:app --reload --port 8000
```

브라우저: http://127.0.0.1:8000

입력원 선택 (환경 변수 또는 실행 인자로 정한다):

| 모드 | 용도 |
|---|---|
| `dummy` | 합성 신호. 하드웨어 없이 화면과 흐름 개발 |
| `replay` | `data/real`의 `raw_serial_log.txt` 재생. 실제 신호로 전처리 디버깅 |
| `serial` | ESP32 직접 연결 (`--port COM6` 기본, 환경에 맞게 변경) |

테스트:

```
pytest
cd web && npm test
```

주의: 시리얼 포트는 한 프로그램만 열 수 있다. Arduino IDE 시리얼 모니터를 닫고 실행한다. 포트를 열면 ESP32가 재부팅되는 것은 정상이다.

## 4. 아키텍처

```
[ESP32 + 센서] ──시리얼 50Hz──▶ [입력원] ─▶ 품질 검사 ─▶ 기준값 ─▶ 특징 추출
      ▲                     (serial/replay/dummy)                       │
      │                                                                ▼
      │ RESULT 명령                                             [교체식 판정기]
      │ (LED, OLED)                                      dummy → rule → logreg
      │                                                                │
      └────────────── 서버 ◀───── 이유 생성 ◀────────────────────────┘
                        │
                        └─ WebSocket ─▶ [웹: 그래프, 결과, 이유, 가상 LED/OLED]
```

핵심 원칙: **판정기만 교체 가능하고 나머지 입출력 형식은 고정이다.** 더미에서 학습 모델로 바꿀 때 서버, 웹, 장치 코드는 바뀌지 않아야 한다.

## 5. 데이터 계약

### 5.1 ESP32 → 서버 (기존 펌웨어 KITEL_MAIN_V5, 변경 금지 기본)

샘플 헤더 (50Hz, 115200 baud):

```
time_ms,fsr,gsr,ir,red,phase,trial,session,baseline_id,seq,contact
```

- `phase`: `BASELINE`, `READY`, `QUESTION`, `RESPONSE`
- 기준 측정 약 15초, 답변 구간 약 20초 (`record_session.py`의 `EXPECTED_MS`)
- 펌웨어는 시험 종료 시 요약 줄(SUMMARY)과 경고 비트(`LOW_SAMPLES`, `LOW_IR`, `FSR_SAT`, `GSR_SAT`, `CONTACT_DIP`, `PPG_OVERFLOW`)를 보낸다.
- 요약 필드 예: `bl_*`(기준), `rs_*`(답변), `fsr_dz`, `gsr_dz`

### 5.2 서버 → ESP32 (LED, OLED 출력, 신규 제안)

펌웨어에 수신 코드를 추가해야 한다. 한 줄 텍스트 명령으로 제안한다.

```
STATE,<READY|BASELINE|ANSWER|ERROR>,<남은초>
RESULT,<TRUTH|LIE|HOLD>,<점수0-100>,<사유코드>
```

규칙: 명령마다 요청 ID를 붙여 장치가 수신 확인(ACK)을 돌려주게 한다. 중복 명령은 한 번만 실행한다. 끊긴 세션의 늦은 결과를 새 세션에 적용하지 않는다.

### 5.3 서버 ↔ 웹 (WebSocket, `INTEGRATION.md` 확장)

서버 → 웹:

| type | 내용 |
|---|---|
| `device_status` | 연결 여부, 입력원(source), 펌웨어 버전 |
| `sample_batch` | 순번, 시간, 원시 채널 값, 품질 플래그 |
| `session_event` | 기준 시작/종료, 질문, 답변, 취소, 완료 |
| `baseline_result` | 기준 평균/표준편차, 채널별 품질 |
| `trial_result` | PRD 7절의 판정 결과 형식 |
| `error` | 재시도 가능 여부와 사용자용 설명 |

웹 → 서버 (명령, 요청 ID 포함):

`start_baseline`, `start_trial {question_id}`, `cancel`, `new_session {participant}`, `set_mode {dummy|replay|serial}`

### 5.4 저장 형식

- `source`: `dummy` 또는 `hardware`로 고정
- 필수 키: `participant_id`, `session_id`, `trial_id`, `device_id`, 펌웨어 버전, 장치 시각, 서버 수신 시각
- 원시 IR/Red/GSR/FSR 값과 ADC 해상도, 기준 전압, 샘플링 주기, 품질 플래그를 보관한다.
- `ground_truth`는 실험 기록에만 둔다.
- 학습되지 않은 상태에서는 `model_version`, `score`를 null로 둔다.

## 6. 판정기 인터페이스

```python
# server/judges/base.py
from dataclasses import dataclass, field
from typing import Protocol, Optional

@dataclass
class Contribution:
    sensor: str          # "ppg" | "gsr" | "fsr"
    feature: str
    value: float         # 표준화된 특징값 (σ)
    contribution: float  # 판정에 기여한 정도 (부호 있음, 양수 = 거짓 쪽)

@dataclass
class Verdict:
    decision: str                    # "truth" | "lie" | "inconclusive"
    score: Optional[float]           # 0~1 또는 None
    trained: bool
    judge_name: str
    judge_version: str
    contributions: list[Contribution] = field(default_factory=list)
    hold_reason: Optional[str] = None

class Judge(Protocol):
    name: str
    version: str
    trained: bool
    def judge(self, features: dict, baseline: dict, quality: dict) -> Verdict: ...
```

구현 순서:

1. `DummyJudge`: 고정된 가짜 계수. 흐름과 화면 확인용. `trained=False`.
2. `RuleJudge`: 개인 기준 대비 z-score가 임계값을 넘은 센서 수로 판정. 시연 예비안. 임계값은 설정 파일에서 읽는다. 데이터가 적어도 쓸 수 있지만 `trained=False`로 둔다.
3. `LogregJudge`: `server/models/model.json`을 읽는다. 내용은 특징 순서, 정규화 값(평균, 표준편차), 계수, 절편, 임계값, 필터 설정, 버전이다. 학습 파이프라인이 만든다.

판정기 선택은 설정 한 줄(`JUDGE=dummy|rule|logreg`)로 바꾼다. 입력에 라벨이 있으면 오류를 낸다.

## 7. 특징 추출

처음에는 6개 안팎으로 시작하고, 후보는 넉넉히 계산하되 모델이 일부만 쓰게 한다.

| 특징 | 출처 | 비고 |
|---|---|---|
| `hr_delta` | IR 대역통과(0.5~5Hz) 후 피크 검출 | `collector/calculate_bpm.py` 참고 |
| `ppg_amp_delta` | 필터된 IR 진폭 | |
| `gsr_max_change` | 중앙값 필터 + 저역통과 후 최대 변화 | 1Hz 저역통과 (`extract_features.py`, 분석 코드 참고) |
| `gsr_dz` | 펌웨어 요약 또는 직접 계산 | |
| `fsr_mean_delta` / `fsr_sd_ratio` | 답변 구간 vs 기준 | |
| `fsr_dz` | 펌웨어 요약 또는 직접 계산 | |

규칙:

- 표준편차 하한과 z-score 클리핑 정책을 정하고 문서화한다.
- 필터는 실시간에도 쓸 수 있는 **인과적 필터**를 기본으로 한다. `filtfilt`처럼 앞뒤를 다 보는 방식은 분석용으로만 쓰고 실시간 경로에서는 쓰지 않는다.
- 학습 시 스케일링, 특징 선택, 하이퍼파라미터 조정은 학습 폴드 안에서만 한다 (데이터 누수 방지).

## 8. 웹 (web/)

- 기존 `Truthpulse_Game`의 화면을 사용한다. 내용: 기준 측정, 질문별 측정, 취소, 20초 그래프, 기록, CSV/JSON 내보내기.
- 바꿀 부분: `demoSample` 공급부를 WebSocket 어댑터로 교체한다. 실제 센서 모드에서는 `summarizeDemo`를 호출하지 않고 서버의 `trial_result`를 그대로 표시한다.
- 추가할 부분: 판정 결과 카드, 이유 목록, 센서별 기여도 막대, 가상 LED/OLED 패널, "모델 미학습(데모)" 배지, 입력원 표시(dummy/replay/serial).
- 기존 `core.mjs`의 품질 검사, CSV 이스케이프, 저장 검증 로직과 테스트는 유지한다.

## 9. 펌웨어 (firmware/)

- 현재 첨부된 폴더에는 펌웨어 소스가 없다. KITEL_MAIN_V5 소스를 `firmware/`에 추가한 뒤 작업한다.
- 추가할 것: OLED 초기화와 표시, LED 제어, 서버 명령 수신(5.2절), ACK 응답.
- 센서 샘플 출력 형식(5.1절)은 바꾸지 않는다. 수집기가 이 형식에 맞춰져 있다.
- I²C 주소 충돌(MAX30102 0x57, OLED 보통 0x3C)과 핀 배정을 `docs/pinmap.md`에 기록한다.

## 10. 테스트

| 대상 | 방법 |
|---|---|
| 수집기 | `python collector/test_replay.py` (저장된 로그 재생, 하드웨어 불필요) |
| 품질 검사, 기준값 | 단위 테스트: 결측, 역순, 끊김, 평평한 신호, 접촉 불량 |
| 라벨 독립성 | 라벨을 바꿔도 특징과 판정이 같아야 한다 |
| 판정기 | 세 판정기가 같은 입출력 형식을 지키는지 |
| 이유 생성 | 기여도가 있는 경우와 보류 경우 문장 확인 |
| 웹 | `npm test` + 브라우저에서 한 사이클 수동 확인 |
| 통합 | 더미 모드로 10회 연속 사이클, 이후 replay 모드, 마지막에 장치 연결 |

## 11. 알려진 이슈

| 번호 | 내용 | 상태 |
|---|---|---|
| I1 | 정상 질문이 `ABORTED`로 처리되어 학습 가능 데이터가 0건이다. 복구 결과를 보면 ESP32는 `valid=1`을 보냈는데 `record_session.py`가 `ABORTED`로 처리했고, 연습 질문만 반복되어 본 질문에 도달하지 못했다. 단서: 시간 간격 최대값(36~40ms)이 `GAP_LIMIT_MS=30`을 넘어 `TIME_GAPS` 경고가 나오고 `integrity=CHECK`가 된다. 원인은 아직 확인하지 않았다. | **P0, 미해결** |
| I2 | z-score가 -22처럼 매우 큰 값으로 나오는 경우가 있다. 표준편차 하한과 클리핑 정책이 필요하다. | 미해결 |
| I3 | 펌웨어 소스가 저장소에 없다. | 미해결 |
| I4 | 웹의 `hr`(심박수) 채널과 수집기의 `ir`(원시) 채널이 다르다. 서버에서 IR로 심박수를 계산해 맞춘다. | 설계됨 |
| I5 | 수집기 폴더에 `record_session_old.py`, 큰 센서 CSV 등 불필요한 파일이 있다. 이 저장소에는 복사하지 않았다. | 해결됨 |

## 12. 단계 체크리스트

완료하면 `[x]`로 바꾸고 날짜를 적는다.

- [ ] **M0** 저장소와 폴더 구조, `.gitignore`, 문서 배치
- [ ] **M1** I1 수정 → `test_replay.py` 통과 → 짧은 실제 세션에서 본 질문 라벨이 남는지 확인 → 본격 수집 시작
- [ ] **M2** 입력원(dummy/replay), 품질 검사, 기준값, 특징 추출, `DummyJudge`, 이유 생성을 명령줄에서 확인
- [ ] **M3** FastAPI + WebSocket, 웹 연결, 가상 LED/OLED 패널, 배지
- [ ] **M4** 펌웨어 LED/OLED 출력, 서버→장치 명령과 ACK
- [ ] **M5** `RuleJudge` (시연 예비안), 임계값 설정 파일
- [ ] **M6** 학습 파이프라인(참가자 분리 검증), `model.json` 생성, `LogregJudge` 교체
- [ ] **M7** 통합 테스트, 리허설, (선택) 저주파 피드백

M2, M3, M5는 실측 데이터 없이 진행할 수 있다. M1의 수집은 개발과 병행해서 계속한다. M6은 라벨이 확정된 실측 데이터가 충분히 모인 뒤에 한다 (제안: 참가자 8~10명 이상, 확정된 질문 100~200개 이상).

## 13. Git과 문서 관리

- 커밋, 푸시, 풀은 사용자가 직접 한다 (1절 규칙).
- 커밋 메시지 형식 제안: `기능: 설명`, `수정: 설명`, `문서: 설명` (예: `수정: 정상 질문이 ABORTED로 처리되는 문제`).
- Claude Code는 작업이 끝날 때 바뀐 파일 목록과 한 줄 커밋 메시지 제안을 알려 준다.
- 깃에 올리지 않을 것: `CLAUDE.md`, `.claude/`, `data/`, `.venv/`, `node_modules/`, `__pycache__/`, `.env`
