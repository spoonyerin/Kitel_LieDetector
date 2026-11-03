# TruthPulse 개발 문서 v0.2

Claude Code는 작업 시작 전에 이 문서를 읽는다. 제품 요구사항은 `docs/PRD.md`에 있다.

**우선순위 원칙:** 기획서(중간발표 PPTX)가 기준이고, 구현 방식이 충돌하면 **LieDetector(`collector/`) 코드가 정한 방식을 따른다.** 기기 펌웨어와 `collector/`의 데이터 구조, 저장 구조를 임의로 바꾸지 않는다.

## 0. 읽어야 할 문서

| 문서 | 내용 | 언제 읽나 |
|---|---|---|
| `docs/PRD.md` | 무엇을 만드는지, 기능 ID(F1~F13), 수락 기준 | 새 기능을 시작할 때 |
| `docs/development.md` (이 문서) | 구조, 규칙, 데이터 계약, 단계 | 항상 |
| `INTEGRATION.md` | 웹과 장치 연동 계약 제안 (기존 웹 프로토타입 문서) | 통신 코드를 만질 때 |
| `collector/record_session.py` 상단 설명 | 수집기 사용법과 저장 구조 | 수집기를 만질 때 |

## 1. 작업 규칙 (반드시 지킬 것)

1. **git은 사용자가 직접 한다.** `git commit`, `git push`, `git pull`, `git merge`, `git rebase`, `git reset`을 실행하지 않는다. 읽기 전용 명령(`git status`, `git diff`, `git log`)만 쓴다. 변경이 끝나면 바뀐 파일 목록과 한 줄 커밋 메시지 제안만 알려 준다.
2. **커밋 메시지나 파일 어디에도 AI 공동작성 표기를 넣지 않는다.**
3. `data/real/` 아래는 읽기만 한다. 수정, 삭제, 덮어쓰기를 하지 않는다.
4. 모든 데이터, 기록, 결과에 `source` 값(`dummy` 또는 `hardware`)을 남긴다. `dummy` 데이터는 학습 코드가 절대 읽지 못하게 한다.
5. 정답 라벨(`ground_truth`, `condition`)은 판정과 특징 추출의 입력으로 쓰지 않는다. 테스트로 보장한다.
6. 판정기가 학습되지 않았으면(`trained=false`) 화면에 "모델 미학습(데모)"를 표시한다. 더미 결과로 정확도나 확률을 계산하거나 표시하지 않는다.
7. **`collector/`의 열 구조, 파일 저장 구조, 기기 출력 형식을 바꾸지 않는다.** 바꿔야 하면 먼저 이유와 영향을 알리고 확인을 받는다.
8. **PC에서 기기로 아무것도 보내지 않는다.** 현재 펌웨어에는 PC→기기 수신 기능이 없다 (실험 로그와 `record_session.py`에서 확인).
9. 큰 변경 전에는 무엇을 바꿀지 짧게 알리고, 기존 동작을 깨는 변경은 먼저 확인한다. **알린 범위 밖의 명령은 실행하지 않는다. 쓰기가 일어날 수 있는 시험은 먼저 알린다.**
10. 새 기능에는 테스트를 같이 추가하고, 끝나면 테스트를 실행해 결과를 알려 준다.
11. 설명과 주석은 한국어로 쓴다.
12. 실측 데이터, 비밀 값, `.env`는 저장소에 올라가지 않게 `.gitignore`를 유지한다.
13. 문서와 코드가 달라지면 문서도 같이 고친다. 특히 11절 이슈 목록과 12절 체크리스트를 갱신한다.

## 2. 폴더 구조

```
Kitel_LieDetector/
├─ CLAUDE.md              ← 로컬 전용 (깃에 올리지 않음). 이 문서를 불러오기만 한다.
├─ INTEGRATION.md
├─ pytest.ini             ← pytest는 tests/만 모은다
├─ requirements.txt       ← pip freeze 결과 (버전 고정)
├─ docs/
│  ├─ PRD.md
│  └─ development.md
├─ collector/             ← LieDetector에서 가져온 수집, 분석 코드 (구조 변경 금지)
│  ├─ record_session.py   ← 수집기. COM 포트로 기기 출력을 읽고 CSV로 저장
│  ├─ extract_features.py
│  ├─ analyze_data.py     ← 필터링 분석용 (실시간 경로 아님)
│  ├─ calculate_bpm.py
│  ├─ recover_data.py
│  ├─ test_replay.py      ← 저장된 로그 재생 검증 (pytest용 아님, 직접 실행)
│  └─ question_bank.csv   ← 질문 은행 15문항
├─ firmware/              ← ESP32 펌웨어 (KITEL_MAIN_V5, 소스 추가 필요)
├─ server/                ← 새로 만드는 Python 서버
│  ├─ app.py              ← FastAPI, WebSocket, 정적 파일 서빙
│  ├─ source/             ← 입력원: serial_source.py, replay_source.py, dummy_source.py
│  ├─ pipeline/           ← 품질 검사, 기준값, 특징 추출
│  ├─ judges/             ← base.py, dummy.py, rule.py, logreg.py
│  ├─ reasons.py          ← 기여도 → 이유 문장
│  ├─ storage.py          ← 기록 저장 (source 표시)
│  └─ models/             ← model.json (학습 결과)
├─ web/                   ← 기존 Truthpulse_Game 화면
│  ├─ dist/               ← 정적 화면 (빌드 없음, 소스이자 배포 루트)
│  ├─ tests/              ← core.mjs 단위 테스트 (npm test)
│  ├─ package.json
│  └─ server.mjs          ← 예전 웹 단독 개발용 서버 (아래 참고)
├─ training/              ← 학습 스크립트 (실측 데이터만 읽음)
├─ tests/                 ← pytest
└─ data/                  ← 깃에 올리지 않음
   ├─ real/               ← 실측 (읽기 전용). 수집기 기록이 여기 있다
   └─ dummy/              ← 더미
```

`web/server.mjs`는 예전 웹 프로토타입을 혼자 띄우던 개발용 정적 서버다(`npm run dev`, 포트 4173, 가상 신호만 사용). 최종 실행은 Python 서버(`server/app.py`)가 `web/dist`를 서빙하는 방식으로 하며, 기기 연동과 WebSocket은 Python 서버에서만 동작한다.

## 3. 개발 환경과 실행 (Windows)

Python 3.13, Node 24 기준이다. 가상환경은 활성화하지 않고 `.venv\Scripts\python.exe -m ...` 형태로 직접 부른다 (PowerShell 실행 정책 문제를 피한다).

처음 한 번:

```
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`pandas`는 **3 미만으로 고정**한다 (`pandas<3`, 현재 2.3.3). 기존 수집·분석 코드와 같은 동작을 유지하려는 선택이며, `tests/test_env.py`가 이를 확인한다. pandas 3으로 올릴 때는 M1 이후 `collector/`를 재검증하고 이 테스트를 고친다.

`requirements.txt`를 새로 만들 때는 파일이 UTF-16으로 저장되지 않게 주의한다 (PowerShell 5.1의 `>`는 UTF-16으로 저장하므로 `Out-File -Encoding ascii`를 쓴다).

서버 실행 (설계 결정: Python 서버가 `web/dist`를 같이 서빙한다. 같은 포트라 CORS 설정이 필요 없다):

```
.venv\Scripts\python.exe -m uvicorn server.app:app --reload --port 8000
```

브라우저: http://127.0.0.1:8000

입력원 선택 (환경 변수 또는 실행 인자):

| 모드 | 용도 |
|---|---|
| `dummy` | 합성 신호. 하드웨어 없이 화면과 흐름 개발 |
| `replay` | `data/real`의 `raw_serial_log.txt` 재생. 실제 신호로 전처리 디버깅 |
| `serial` | 기기 직접 연결 (COM 포트, 속도 115200, 포트 번호는 환경에 맞게) |

테스트:

```
.venv\Scripts\python.exe -m pytest
cd web && npm test
```

수집기 테스트는 따로 실행한다 (현재 폴더 기준으로 `experiments/`를 찾으므로 11절 I8 참고):

```
.venv\Scripts\python.exe collector\test_replay.py
```

서버는 시작할 때 `data/dummy/`가 없으면 자동으로 만든다 (`data/`가 gitignore 대상이라 새로 받은 저장소에는 폴더가 없다). `data/real/`은 만들거나 쓰지 않는다. 기기(serial)로 받은 기록의 저장 위치는 11절 I6에서 정한다.

주의: COM 포트는 한 프로그램만 열 수 있다. 수집기와 서버, Arduino 시리얼 모니터를 동시에 열지 않는다. 포트를 열면 ESP32가 재부팅되는 것은 정상이다.

## 4. 아키텍처

현재 구조는 **기기 주도, PC 읽기 전용**이다.

```
[ESP32 + 센서 + 버튼 + LED/OLED] ──COM 포트 50Hz──▶ [입력원: serial / replay / dummy]
      ▲ 버튼으로 진행 (PC는 아무것도 보내지 않음)            │
                                                          ▼
                                    품질 검사 ─▶ 기준값 ─▶ 특징 추출
                                                          │
                                                          ▼
                                                 [교체식 판정기] dummy → rule → logreg
                                                          │
                                                          ▼
                                                     이유 생성
                                                          │
                                    WebSocket ◀───────────┘
                                         ▼
                        [웹: 단계 표시, 그래프, 결과, 이유]  (보기 전용)
```

실험 모드는 기존 `collector/record_session.py`가 같은 입력을 받아 저장하는 방식을 그대로 쓴다.

최종 단계(PRD F12)에서는 검증된 전처리·계수·임계값을 펌웨어로 옮기고 Python은 분석·재학습·검증에 쓴다. 이때 기기가 판정 결과를 PC로 보내는 줄 형식을 새로 정해야 하며, 펌웨어 담당자와 함께 정한다.

## 5. 데이터 계약

### 5.1 기기 → PC (기존 펌웨어 KITEL_MAIN_V5, 변경 금지)

PC 로그(`raw_serial_log.txt`)에는 `PC시각<TAB>기기가 보낸 줄`이 저장된다. 기기가 보내는 줄은 세 종류다.

**샘플** (50Hz, 11개 열):

```
time_ms,fsr,gsr,ir,red,phase,trial,session,baseline_id,seq,contact
```

예: `62441,773,2233,105696,90223,BASELINE,0,1,1,3095,1`

- `phase`: `BASELINE`, `READY`, `QUESTION`, `RESPONSE`
- 기준 측정 약 15초, 답변 구간 약 20초 (`record_session.py`의 `EXPECTED_MS`)
- 샘플 간격 20ms, 허용 간격 상한 `GAP_LIMIT_MS=30` (11절 I1 참고)

**이벤트**: `#EVENT,<esp_time_ms>,<이름>,<trial>,<detail>,<session>,<baseline_id>`

실험 로그에서 확인된 이름: `BUTTON`, `PLACE_HAND`, `HAND_OK`, `HAND_LOST`, `SESSION`, `SESSION_END`, `BASELINE`, `BASELINE_LOST`, `READY`, `QUESTION`, `RESPONSE`, `ANALYZING`, `RESULT`, `CONTACT_DIP`, `CONTACT_SHIFT`, `CONTACT_BACK`, `ABORTED`, `INVALID`.

버튼 `detail`은 `<단계>:<PRESS|SHORT|LONG>:<동작>:<IRQ|POLL>` 형식이다. 예: `PLACE_HAND:PRESS:START_SESSION`, `READY:PRESS:QUESTION`, `QUESTION:PRESS:ANSWER`, `RESULT:SHORT:NEXT_QUESTION`, `RESULT:LONG:END_SESSION`, `PLACE_HAND:SHORT:REBASELINE`, `RESPONSE:SHORT:IGNORED_TIMED_PHASE`.

**요약**: 기준 측정과 시험이 끝날 때 한 줄씩.

- `#BASELINE,` + `session, baseline_id, valid, reason, warn_flags, n, fsr_mean, fsr_sd, gsr_mean, gsr_sd, ir_mean`
- `#SUMMARY,` + `session, baseline_id, trial, valid, reason, warn_flags, bl_n, bl_fsr_mean, bl_fsr_sd, bl_gsr_mean, bl_gsr_sd, bl_ir_mean, q_n, q_ms, rs_n, rs_fsr_mean, rs_fsr_sd, rs_gsr_mean, rs_gsr_sd, rs_ir_mean, fsr_dz, gsr_dz`
- `nan` 값이 올 수 있고, `reason`에 `HAND_OFF_LONG` 같은 사유가 들어간다.
- 경고 비트: `LOW_SAMPLES`, `LOW_IR`, `FSR_SAT`, `GSR_SAT`, `CONTACT_DIP`, `PPG_OVERFLOW`

서버의 입력원은 `collector/record_session.py`의 줄 해석 코드를 **새로 짜지 않고 재사용**한다. 구조가 어긋나지 않게 하려는 규칙이다.

### 5.2 PC 저장 구조 (수집기, 변경 금지)

```
data/real/experiments/run_<날짜시각>/
   raw_serial_log.txt, run_events.csv, all_trials_summary.csv, all_baselines.csv
   <session_id>/
      protocol.csv, trials_summary.csv, baselines.csv
      participant_<ID>_baseline01_samples.csv
      participant_<ID>_trial01_samples.csv / _events.csv
```

시험 샘플 CSV 열: `session_id, participant_id, trial_id, baseline_id, question_id, condition, time_ms, fsr, gsr, ir, red, phase, seq, contact`

### 5.3 서버 ↔ 웹 (WebSocket, 보기 전용)

서버 → 웹:

| type | 내용 |
|---|---|
| `device_status` | 연결 여부, 입력원(source) |
| `phase_update` | 현재 단계, 이벤트(손 접촉, 접촉 문제, 취소 등) |
| `sample_batch` | 순번, 시간, 원시 채널 값, 품질 플래그 |
| `baseline_result` | 기준 평균/표준편차, 채널별 품질 |
| `trial_result` | PRD 9절의 판정 결과 형식 |
| `error` | 재시도 가능 여부와 사용자용 설명 |

웹 → 서버 (요청 ID 포함, 기기 제어 없음): `set_mode {dummy|replay|serial}`, `new_session {participant}` (서버 내부에서만 사용)

### 5.4 저장 규칙

- `source`: `dummy` 또는 `hardware`로 고정
- 필수 키: `participant_id`, `session_id`, `trial_id`, 기기 시각(`time_ms`), 서버 수신 시각
- 원시 IR/Red/GSR/FSR 값과 품질 플래그를 보관한다.
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
2. `RuleJudge`: 개인 기준 대비 z-score가 임계값을 넘은 센서의 수와 크기로 판정. 시연 예비안. 임계값은 설정 파일에서 읽는다. `trained=False`.
3. `LogregJudge`: `server/models/model.json`을 읽는다. 내용은 특징 순서, 정규화 값, 계수, 절편, 임계값, 필터 설정, 버전이다. 학습 파이프라인이 만든다. 기획서대로 로지스틱 회귀이며 `fit()`, `predict_proba()`를 쓴다.

판정기 선택은 설정 한 줄(`JUDGE=dummy|rule|logreg`)로 바꾼다. 입력에 라벨이 있으면 오류를 낸다.

## 7. 전처리와 특징 추출

전처리 시작값은 `collector/analyze_data.py`를 따른다.

| 센서 | 처리 |
|---|---|
| FSR | 5샘플 중앙값 + 5Hz 저역통과 |
| GSR | 5샘플 중앙값 + 1Hz 저역통과 |
| PPG | 0.5~5Hz 대역통과 (초기 실험용 범위) |

실시간 경로에서는 인과적 필터만 쓴다. `filtfilt`는 분석용이다. 샘플링 속도는 기기 시각으로 추정한다 (`fs = (샘플 수 - 1) / 측정 시간`).

특징 후보 (처음에는 6개 안팎):

| 특징 | 출처 |
|---|---|
| `hr_delta` | IR 피크 기반 (`collector/calculate_bpm.py` 참고) |
| `ppg_amp_delta` | 필터된 IR 진폭 |
| `gsr_max_change`, `gsr_dz` | 답변 구간 vs 기준 / 기기 요약 |
| `fsr_mean_delta`, `fsr_sd_ratio`, `fsr_dz` | 답변 구간 vs 기준 / 기기 요약 |
| 반응 지속시간 | 기획서의 특징값 후보 |

표준편차 하한과 z-score 클리핑 정책을 정한다 (I2). 학습 시 스케일링, 특징 선택, 하이퍼파라미터 조정은 학습 폴드 안에서만 한다.

## 8. 웹 (web/)

- 기존 `Truthpulse_Game` 화면을 사용한다. 기록, CSV/JSON 내보내기, 20초 그래프, 접촉 불량 처리는 유지한다.
- 바꿀 부분: `demoSample` 공급부를 WebSocket 어댑터로 교체한다. 기기 연동 모드에서는 `summarizeDemo`를 호출하지 않고 서버의 `trial_result`를 그대로 표시한다.
- 기존의 세션 제어 버튼(기준 측정 시작, 질문 시작, 취소)은 기기 주도 구조에서는 서버로 보내지 않는다. 기기 단계를 따라가는 표시로 바꾼다.
- 추가할 부분: 현재 단계 표시, 판정 결과 카드, 이유 목록, 센서별 기여도 막대, "모델 미학습(데모)" 배지, 입력원 표시.

## 9. 펌웨어 (firmware/)

- 펌웨어 소스(KITEL_MAIN_V5)는 현재 저장소에 없다. 추가한 뒤 작업한다 (I3).
- 확인할 것: LED/OLED 판정·표시 기준, 연결 방식, `ANALYZING`/`RESULT` 단계의 동작, 재부팅 원인.
- 샘플 출력 형식(5.1절)은 바꾸지 않는다.
- 기기 판정 결과를 PC로 보내려면 줄 형식을 새로 정해야 한다 (F11, F12). 펌웨어 담당자와 협의한 뒤 정한다.

## 10. 테스트

| 대상 | 방법 |
|---|---|
| 환경 | `pytest` (`tests/test_env.py`: 파이썬 버전, 패키지 임포트, pandas<3) |
| 수집기 | `collector\test_replay.py` (저장된 로그 재생, 하드웨어 불필요) |
| 줄 해석 | 샘플, `#EVENT`, `#BASELINE`, `#SUMMARY`, `nan` 처리 단위 테스트 |
| 품질 검사, 기준값 | 결측, 역순, 끊김, 평평한 신호, 접촉 불량 |
| 라벨 독립성 | 라벨을 바꿔도 특징과 판정이 같아야 한다 |
| 판정기 | 세 판정기가 같은 입출력 형식을 지키는지 |
| 이유 생성 | 기여도가 있는 경우와 보류 경우 문장 확인 |
| 웹 | `npm test` + 브라우저에서 한 사이클 확인 |
| 통합 | 더미 모드 10회 연속, replay 모드, 마지막에 장치 연결 |

## 11. 알려진 이슈

| 번호 | 내용 | 상태 |
|---|---|---|
| I1 | 정상 질문이 `ABORTED`로 처리되어 학습 가능 데이터가 0건이다. 복구 결과를 보면 ESP32는 `valid=1`을 보냈는데 `record_session.py`가 `ABORTED`로 처리했고, 연습 질문만 반복되어 본 질문에 도달하지 못했다. 단서: 시간 간격 최대값(36~40ms)이 `GAP_LIMIT_MS=30`을 넘어 `TIME_GAPS` 경고가 나오고 `integrity=CHECK`가 된다. 원인은 아직 확인하지 않았다. | **P0, 미해결** |
| I2 | z-score가 -22처럼 매우 큰 값으로 나오는 경우가 있다. 표준편차 하한과 클리핑 정책이 필요하다. | 미해결 |
| I3 | 펌웨어 소스가 저장소에 없다. | 미해결 |
| I4 | 웹의 `hr`(심박수) 채널과 수집기의 `ir`(원시) 채널이 다르다. 서버에서 IR로 심박수를 계산해 맞춘다. | 설계됨 |
| I6 | 서버가 기기(serial)로 받은 기록을 어디에 저장할지 정해지지 않았다. `data/real/`은 읽기 전용이고 `data/dummy/`에는 섞으면 안 된다. M4 전에 정한다. | 미해결 |
| I7 | 실험 로그(`run_20261103_154858`)에 ESP32 재부팅 기록(`Guru Meditation Error: Core 1 panic'ed (LoadProhibited)`)이 1건 있다. | 펌웨어 확인 필요 |
| I8 | `collector/test_replay.py`가 `experiments/`를 현재 폴더 기준으로 찾아서, 저장소 루트에서 실행하면 T8, T9가 건너뛰어진다. | M1에서 다룸 |
| I9 | 기기 연결이 블루투스인지 USB인지, 기기가 LED/OLED 참/거짓을 어떻게 판정하는지 알 수 없다. | 펌웨어 확인 필요 |

## 12. 단계 체크리스트

완료하면 `[x]`로 바꾸고 날짜를 적는다.

- [x] **M0** 저장소, 폴더 구조, `.gitignore`, 문서, 개발 환경(`.venv`, `requirements.txt`, `pytest.ini`) (2026-11-04)
- [ ] **M1** I1 수정 → `test_replay.py` 통과 (I8 포함) → 짧은 실제 세션에서 본 질문 라벨이 남는지 확인 → 본격 수집 시작
- [ ] **M2** 입력원(dummy/replay), 줄 해석 재사용, 품질 검사, 기준값, 특징 추출, `DummyJudge`, 이유 생성을 명령줄에서 확인
- [ ] **M3** FastAPI + WebSocket, 웹 연결(보기 전용), 단계 표시, 배지
- [ ] **M4** 펌웨어 소스 확인 후 기기 결과와 서버 판정 일치 방안 결정, I6 결정
- [ ] **M5** `RuleJudge` (시연 예비안), 임계값 설정 파일
- [ ] **M6** 학습 파이프라인(참가자 분리 검증), `model.json`, `LogregJudge` 교체
- [ ] **M7** 통합 테스트, 리허설, (선택) ESP32 이식과 저주파 피드백

M2, M3, M5는 실측 데이터 없이 진행할 수 있다. M1의 수집은 개발과 병행해서 계속한다. M6은 라벨이 확정된 실측 데이터가 충분히 모인 뒤에 한다 (제안: 참가자 8~10명 이상, 확정된 질문 100~200개 이상).

## 13. Git과 문서 관리

- 커밋, 푸시, 풀은 사용자가 직접 한다 (1절 규칙). 기능마다 새 브랜치에서 작업하고, 테스트가 통과한 뒤에만 합친다. main에 직접 커밋하지 않는다.
- 커밋 메시지 형식 제안: `기능: 설명`, `수정: 설명`, `문서: 설명`
- 깃에 올리지 않을 것: `CLAUDE.md`, `.claude/`, `data/`, `.venv/`, `node_modules/`, `__pycache__/`, `.env`, `.pytest_cache/`, `.vscode/`
