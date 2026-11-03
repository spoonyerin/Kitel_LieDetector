# TruthPulse 개발 문서 v0.3

- 갱신일: 2026-11-04
- v0.2에서 바뀐 점: M2 구현(S1~S7) 결과를 반영했다. 실제 폴더 구조, 실행 방법(dummy/replay/serial), 서버↔웹 메시지, 판정기(RuleJudge, 국소 dz), 심박 추정값, 알려진 한계와 앞으로 할 일을 정리했다. 구현되지 않은 항목(서버 기록 저장, 학습 판정기 등)은 "미구현"으로 표시했다.

Claude Code는 작업 시작 전에 이 문서를 읽는다. 제품 요구사항은 `docs/PRD.md`에 있다.

**우선순위 원칙:** 기획서(중간발표 PPTX)가 기준이고, 구현 방식이 충돌하면 **LieDetector(`collector/`) 코드가 정한 방식을 따른다.** 기기 펌웨어와 `collector/`의 데이터 구조, 저장 구조를 임의로 바꾸지 않는다.

## 0. 읽어야 할 문서

| 문서 | 내용 | 언제 읽나 |
|---|---|---|
| `docs/PRD.md` | 무엇을 만드는지, 기능 ID(F1~F13), 수락 기준 | 새 기능을 시작할 때 |
| `docs/development.md` (이 문서) | 구조, 규칙, 데이터 계약, 단계 | 항상 |
| `INTEGRATION.md` | 웹과 장치 연동 계약 제안 (기존 웹 프로토타입 문서) | 통신 코드를 만질 때 |
| `collector/record_session.py` 상단 설명 | 수집기 사용법과 저장 구조 | 수집기를 만질 때 |
| `server/config.py` 상단 표 | 서버 환경 변수 전체 목록과 기본값 | 서버를 실행하거나 설정을 바꿀 때 |

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

## 2. 폴더 구조 (2026-11-04 기준 실제 파일)

```
Kitel_LieDetector/
├─ CLAUDE.md              ← 로컬 전용 (깃에 올리지 않음). 이 문서를 불러오기만 한다.
├─ INTEGRATION.md
├─ pytest.ini             ← pytest는 tests/만 모은다
├─ requirements.txt       ← pip freeze 결과 (버전 고정)
├─ docs/
│  ├─ PRD.md
│  └─ development.md
├─ collector/             ← LieDetector에서 가져온 수집, 분석 코드 (구조 변경 금지, 서버는 import만 함)
│  ├─ record_session.py   ← 수집기. COM 포트로 기기 출력을 읽고 CSV로 저장
│  ├─ extract_features.py
│  ├─ analyze_data.py     ← 필터링 분석용 (실시간 경로 아님)
│  ├─ calculate_bpm.py
│  ├─ recover_data.py     ← parse_raw_log: 서버 줄 해석기의 비교 기준 (테스트에서 사용)
│  ├─ test_replay.py      ← 저장된 로그 재생 검증 (pytest용 아님, 직접 실행)
│  └─ question_bank.csv   ← 질문 은행 15문항
├─ firmware/              ← ESP32 펌웨어 (KITEL_MAIN_V5, 소스 추가 필요)
├─ server/                ← Python 서버 (M2에서 구현)
│  ├─ app.py              ← FastAPI, /ws 방송, web/dist 정적 서빙, 입력원 스레드
│  ├─ config.py           ← 환경 변수 설정 (상단에 전체 표)
│  ├─ cli.py              ← 명령줄 도구: parse, replay, dummy, bpm, ports (모두 읽기 전용)
│  ├─ result.py           ← 시도 하나 판정 → trial_result (PRD 9절 형식)
│  ├─ reasons.py          ← 기여도/보류 코드 → 쉬운 한국어 문장
│  ├─ stream.py           ← sample_batch(그래프 샘플 묶음), baseline_result
│  ├─ questions.py        ← 게임 모드 질문 순서와 시도별 배정 (라벨 없음, 8.1절)
│  ├─ source/
│  │  ├─ lines.py         ← 기기 줄 해석기 (collector 상수·함수 재사용, 파일 안 씀)
│  │  ├─ pacing.py        ← 재생 속도 맞춤 (목표 시각 방식)
│  │  ├─ replay_source.py ← raw_serial_log.txt 재생 (읽기 전용, source=hardware)
│  │  ├─ dummy_source.py  ← 기기 형식의 합성 줄 (source=dummy)
│  │  └─ serial_source.py ← COM 포트 읽기 전용 (DTR/RTS 끔, 끊기면 재연결)
│  ├─ pipeline/
│  │  ├─ tracker.py       ← 세션/기준/시도 추적 (collector Run과 같은 상태 규칙)
│  │  ├─ signal.py        ← 인과적 전처리 (중앙값, 저역/대역통과, sosfilt)
│  │  ├─ features.py      ← 국소 dz(판정용) + 전체 특징(학습용, 7.4절)
│  │  ├─ quality.py       ← 최소 품질 검사 (보류 코드)
│  │  └─ bpm.py           ← IR 심박 추정 (화면 표시용, 판정에 안 씀)
│  └─ judges/
│     ├─ base.py          ← Contribution, Verdict, Judge, 라벨 키 거부
│     └─ rule.py          ← RuleJudge
├─ web/                   ← 기존 Truthpulse_Game 화면
│  ├─ dist/               ← 정적 화면 (빌드 없음, 소스이자 배포 루트)
│  │  ├─ index.html, styles.css, core.mjs (기존 데모)
│  │  ├─ app.mjs          ← 기존 데모 + 기기 연동 모드 (/ws 연결 시)
│  │  └─ live.mjs         ← /ws 연결, 자동 재연결, 화면용 순수 함수
│  ├─ tests/              ← core.test.mjs, live.test.mjs (npm test)
│  ├─ package.json
│  └─ server.mjs          ← 예전 웹 단독 개발용 서버 (아래 참고)
├─ training/              ← 학습 파이프라인 (S12, 7.5절)
│  ├─ build_dataset.py    ← collector 기록 → 시도별 특징 + 확정 라벨 (서버 코드 재사용)
│  ├─ train_logreg.py     ← 참가자 분리 검증, 기준 모델 비교, model.json 내보내기
│  └─ out/                ← 출력 (gitignore 대상)
├─ tests/                 ← pytest (test_lines, test_tracker, test_sources, test_features, test_quality,
│                            test_judges, test_reasons, test_app, test_stream, test_serial_source, test_bpm,
│                            test_questions, test_env)
└─ data/                  ← 깃에 올리지 않음
   ├─ real/               ← 실측 (읽기 전용). 수집기 기록이 여기 있다
   └─ dummy/              ← 더미 (현재 서버는 아무것도 저장하지 않으므로 쓰지 않는다)
```

아직 없는 것: `server/storage.py`(기록 저장, I6), `server/judges/logreg.py`(M6, model.json을 읽는 판정기), `server/judges/dummy.py`(RuleJudge를 먼저 만들어 필요 없어짐, 6절).

`web/server.mjs`는 예전 웹 프로토타입을 혼자 띄우던 개발용 정적 서버다(`npm run dev`, 포트 4173, 가상 신호만 사용). `/ws`가 없으므로 웹은 기존 데모 모드로 동작한다. 기기 연동은 Python 서버(`server/app.py`)가 `web/dist`를 같은 포트로 서빙할 때만 동작한다.

## 3. 개발 환경과 실행 (Windows, PowerShell)

Python 3.13, Node 24 기준이다. 가상환경은 활성화하지 않고 `.venv\Scripts\python.exe -m ...` 형태로 직접 부른다 (PowerShell 실행 정책 문제를 피한다). 아래 명령은 모두 저장소 루트에서 실행한다.

처음 한 번:

```
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`pandas`는 **3 미만으로 고정**한다 (`pandas<3`, 현재 2.3.3). 기존 수집·분석 코드와 같은 동작을 유지하려는 선택이며, `tests/test_env.py`가 이를 확인한다. pandas 3으로 올릴 때는 M1 이후 `collector/`를 재검증하고 이 테스트를 고친다. M2의 서버는 새 패키지 없이 requirements의 fastapi, uvicorn, websockets, numpy, scipy, pyserial만 쓴다 (httpx는 없으므로 서버 테스트는 임시 uvicorn + websockets 클라이언트로 한다).

`requirements.txt`를 새로 만들 때는 파일이 UTF-16으로 저장되지 않게 주의한다 (PowerShell 5.1의 `>`는 UTF-16으로 저장하므로 `Out-File -Encoding ascii`를 쓴다).

### 3.1 서버 실행

Python 서버가 `web/dist`를 같이 서빙한다 (같은 포트라 CORS 설정이 필요 없다). 브라우저: http://127.0.0.1:8000 . 끌 때는 Ctrl+C.

| 입력원 | 명령 | 화면의 입력원 이름 |
|---|---|---|
| 더미 (합성 신호) | `$env:SOURCE="dummy"; $env:DUMMY_SPEED="2"; .venv\Scripts\python.exe -m uvicorn server.app:app --port 8000` | 합성 신호(더미) |
| 재생 (저장된 로그) | `$env:SOURCE="replay"; $env:REPLAY_RUN="data\real\experiments\run_20261103_161545"; $env:REPLAY_SPEED="4"; .venv\Scripts\python.exe -m uvicorn server.app:app --port 8000` | 저장된 로그 재생 |
| 시리얼 (기기) | `$env:SOURCE="serial"; $env:SERIAL_PORT="COM6"; .venv\Scripts\python.exe -m uvicorn server.app:app --port 8000` | 실시간 기기 / 기기 연결됨 · 신호 대기 / 기기 연결 끊김 |

- 환경 변수 전체 목록과 기본값은 `server/config.py` 상단 표에 있다. 주요 값: `REPLAY_MAX_WAIT`(세션 사이 공백 단축, 기본 2초), `DUMMY_TRIALS`, `DUMMY_PATTERNS`(calm, reactive, poor), `SERIAL_BAUD`(기본 115200, `collector/record_session.py`의 `--baud` 기본값), `SERIAL_RETRY_SEC`(1~2초), `SERIAL_IDLE_SEC`(기본 5초), `SAMPLE_BATCH_SEC`(0.2~0.5초), `BPM_INTERVAL_SEC`(0.5~1초), `QUESTION_SEED`(질문 순서 시드, 없으면 서버 시작마다 무작위이며 서버 콘솔에 찍힌다), `QUESTION_BANK`(기본 `collector/question_bank.csv`).
- PowerShell의 `$env:` 값은 그 창을 닫을 때까지 남는다. 다른 입력원으로 바꿀 때는 값을 다시 지정하거나 `Remove-Item Env:SOURCE`로 지운다.
- 재생과 더미는 화면에 "실시간"으로 표시하지 않는다. serial은 기기 줄을 실제로 받는 동안만 `live=true`("실시간 기기")다.
- 서버는 파일을 쓰지 않는다. `data/dummy/`도 만들지 않는다 (기록 저장은 미구현, I6).

### 3.2 시리얼(기기) 사용 시 주의

- COM 포트는 한 프로그램만 열 수 있다. 수집기, 서버, Arduino 시리얼 모니터를 동시에 열지 않는다.
- 서버는 포트를 열기 전에 DTR/RTS를 꺼서 ESP32가 재부팅되지 않게 한다 (수집기는 반대로 DTR을 토글해 일부러 재부팅한다). 실제 보드에서 재부팅이 막히는지는 아직 확인 기록이 없다 (I11). 재부팅되면 진행 중인 시도는 INTERRUPTED로 닫히고 "답변 중에 기기가 다시 시작돼서…"로 보류된다.
- 재부팅하지 않으므로 헤더 줄이 오지 않을 수 있고, 이때는 줄 해석기의 `fallback_v5` 규칙을 쓴다.
- **서버를 먼저 켜고 기기에서 세션을 시작한다.** 세션 도중에 서버를 켜면 그 세션의 `SESSION START`를 놓쳐 다음 세션까지 그래프·판정이 나오지 않는다 (I12).
- 포트 목록(포트를 열지 않음): `.venv\Scripts\python.exe -m server.cli ports`

### 3.3 명령줄 도구 (모두 읽기 전용)

```
.venv\Scripts\python.exe -m server.cli parse  data\real\experiments\run_20261103_161545
.venv\Scripts\python.exe -m server.cli replay data\real\experiments\run_20261103_161545 --speed 0 --brief --judge
.venv\Scripts\python.exe -m server.cli replay data\real\experiments\run_20261103_161545 --speed 0 --judge --json
.venv\Scripts\python.exe -m server.cli dummy  --trials 4 --patterns calm,reactive,poor,calm --speed 0 --judge
.venv\Scripts\python.exe -m server.cli bpm    data\real\experiments\run_20261103_161545
.venv\Scripts\python.exe -m server.cli ports
.venv\Scripts\python.exe -m server.cli questions --seed 7 --sessions 2
.venv\Scripts\python.exe -m server.cli features data\real\experiments\run_20261103_161545 --out C:\temp\features.csv
.venv\Scripts\python.exe -m training.build_dataset                (data/real → training/out/dataset.csv)
.venv\Scripts\python.exe -m training.train_logreg                 (데이터셋을 만들어 학습·평가·model.json)
.venv\Scripts\python.exe -m training.train_logreg --data training\out\dataset.csv --out training\out\model.json [--allow-small]
```

`features`는 시도별 전체 특징을 CSV로 쓴다 (출력이 `data/real/`, `collector/` 안이면 거부, 종료 코드 2). `--fsr-th`, `--gsr-th`, `--min-sensors`는 임계값 후보를 비교하는 탐색용이며 `rule.py`의 상수를 바꾸지 않는다.

### 3.4 테스트

```
.venv\Scripts\python.exe -m pytest
cd web; npm test
```

2026-11-04 기준 pytest 253개, npm test 26개 통과. 실측 로그가 없으면 해당 테스트는 건너뛴다. 서버 테스트는 127.0.0.1의 빈 포트에 임시 uvicorn을 띄우고, 시리얼 테스트는 가짜 시리얼만 써서 실제 COM 포트를 열지 않는다.

수집기 테스트는 따로 실행한다 (현재 폴더 기준으로 `experiments/`를 찾으므로 11절 I8 참고):

```
.venv\Scripts\python.exe collector\test_replay.py
```

### 3.5 화면 시험 방법 (사람이 확인)

1. 재생(4배속)으로 서버를 켜고 브라우저를 연다. 확인: 입력원 "저장된 로그 재생", 단계가 기준 측정 → 질문 중 → 답변 측정 중 → 결과로 바뀜, 세션 표시 "세션 S01" → "세션 S01 · 종료" → "세션 S02", 결과 카드에 진실/거짓/보류, 이유, "모델 미학습(데모)", 임계값 안내, 오락용 안내, 보류 사유(예: "손 위치가 바뀌어 이 답변은 측정에서 제외했어요."), PPG 카드의 "BPM 추정값".
2. 새로고침해도 지금까지의 결과, 그래프, 마지막 심박 추정값이 다시 채워지는지 본다.
3. 서버를 Ctrl+C로 끄면 주황색 "연결이 끊겼습니다" 띠, 다시 켜면 자동 재연결.
4. 더미로 한 번 더 확인 (입력원 "합성 신호(더미)", calm 시도 심박 약 72).
5. 기기: 3.2절 주의를 지키고 시리얼로 켠다. 한 사이클, 답변 중 손 위치 바꾸기(접촉 보류), 5초 이상 대기("신호 대기"), USB 뽑았다 꽂기(오류 띠와 재연결), 없는 포트(COM99) 오류 표시를 확인한다.
6. `npm run dev`(포트 4173)로 예전 데모가 그대로 동작하는지 본다.

## 4. 아키텍처 (구현됨)

현재 구조는 **기기 주도, PC 읽기 전용**이다.

```
[ESP32 + 센서 + 버튼 + LED/OLED] ──COM 포트 50Hz──▶ [입력원: serial / replay / dummy]  (모두 기기와 같은 줄 형식)
      ▲ 버튼으로 진행 (PC는 아무것도 보내지 않음)            │
                                                          ▼
                                        줄 해석기 (collector 상수·규칙 재사용)
                                                          │
                              ┌───────────────────────────┼──────────────────────────┐
                              ▼                           ▼                          ▼
                     샘플 묶음 (그래프)          진행 추적기 (세션/기준/시도)       심박 추정 (IR, 표시용)
                              │                           │ 시도 끝                   │
                              │                           ▼                          │
                              │              국소 dz → 품질 검사 → RuleJudge → 이유   │
                              │                           │                          │
                              └──────────────▶ WebSocket /ws 방송 ◀───────────────────┘
                                                          ▼
                        [웹: 입력원, 단계, 세션, 그래프, 결과 카드, 이유, 배지, 심박 추정값]  (보기 전용)
```

- 입력원은 별도 스레드에서 돌고, 메시지는 이벤트 루프로 넘겨 접속자 전원에게 방송한다. 새 접속자에게는 상태, 마지막 오류, 기준 측정, 마지막 단계, 마지막 심박 추정값, 최근 약 20초의 샘플, 지금까지의 시도 결과(최대 200개)를 먼저 보낸다.
- 심박 추정값은 판정 경로와 분리되어 있다 (판정 코드에서 쓰지 않음을 테스트로 확인).
- 실험 모드(데이터 수집)는 기존 `collector/record_session.py`를 그대로 쓴다. 서버와 같은 포트를 동시에 열 수 없다.
- 최종 단계(PRD F12)에서는 검증된 전처리·계수·임계값을 펌웨어로 옮기고 Python은 분석·재학습·검증에 쓴다. 이때 기기가 판정 결과를 PC로 보내는 줄 형식을 새로 정해야 하며, 펌웨어 담당자와 함께 정한다.

## 5. 데이터 계약

### 5.1 기기 → PC (기존 펌웨어 KITEL_MAIN_V5, 변경 금지)

PC 로그(`raw_serial_log.txt`)에는 `PC시각<TAB>기기가 보낸 줄`이 저장된다. 기기가 보내는 줄은 세 종류다.

**샘플** (50Hz, 11개 열):

```
time_ms,fsr,gsr,ir,red,phase,trial,session,baseline_id,seq,contact
```

예: `62441,773,2233,105696,90223,BASELINE,0,1,1,3095,1`

- `phase`: `BASELINE`, `READY`, `QUESTION`, `RESPONSE`. `PLACE_HAND`, `ANALYZING`, `RESULT` 단계에는 샘플을 보내지 않는다 (실측 로그에서 확인).
- 기준 측정 약 15초, 답변 구간 약 20초 (`record_session.py`의 `EXPECTED_MS`)
- 샘플 간격 20ms, 허용 간격 상한 `GAP_LIMIT_MS=30`. 실측에서는 36~40ms 간격이 시도마다 나와 모든 시도가 `integrity=CHECK`다 (I10). 이 경고는 상태(COMPLETE/ABORTED)에 영향이 없다.

**이벤트**: `#EVENT,<esp_time_ms>,<이름>,<trial>,<detail>,<session>,<baseline_id>`

실험 로그에서 확인된 이름: `BUTTON`, `PLACE_HAND`, `HAND_OK`, `HAND_LOST`, `SESSION`, `SESSION_END`, `BASELINE`, `BASELINE_LOST`, `READY`, `QUESTION`, `RESPONSE`, `ANALYZING`, `RESULT`, `CONTACT_DIP`, `CONTACT_SHIFT`, `CONTACT_BACK`, `ABORTED`, `INVALID`.

- 세션 밖의 `PLACE_HAND`(세션 시작 전, 끝난 뒤)는 세션 번호 0으로 온다. 서버는 화면에 기기 번호 대신 추적기의 세션 순번(S01, S02)을 보낸다.
- 세션 종료: `#EVENT,…,SESSION,<trial>,END:LONG_PRESS,…`

버튼 `detail`은 `<단계>:<PRESS|SHORT|LONG>:<동작>:<IRQ|POLL>` 형식이다. 예: `PLACE_HAND:PRESS:START_SESSION`, `READY:PRESS:QUESTION`, `QUESTION:PRESS:ANSWER`, `RESULT:SHORT:NEXT_QUESTION`, `RESULT:LONG:END_SESSION`, `PLACE_HAND:SHORT:REBASELINE`, `RESPONSE:SHORT:IGNORED_TIMED_PHASE`, `READY:PRESS:IGNORED_NO_HAND`, `ABORTED:SHORT:NEED_REBASELINE`.

**요약**: 기준 측정과 시험이 끝날 때 한 줄씩.

- `#BASELINE,` + `session, baseline_id, valid, reason, warn_flags, n, fsr_mean, fsr_sd, gsr_mean, gsr_sd, ir_mean` (다음 시도의 `READY START` 직후에 온다)
- `#SUMMARY,` + `session, baseline_id, trial, valid, reason, warn_flags, bl_n, bl_fsr_mean, bl_fsr_sd, bl_gsr_mean, bl_gsr_sd, bl_ir_mean, q_n, q_ms, rs_n, rs_fsr_mean, rs_fsr_sd, rs_gsr_mean, rs_gsr_sd, rs_ir_mean, fsr_dz, gsr_dz` (`ANALYZING START`와 `RESULT START` 사이에 온다)
- **기기 dz 식 (실측으로 확인):** `fsr_dz = (rs_fsr_mean - bl_fsr_mean) / bl_fsr_sd`, `gsr_dz`도 같다. 예: 161545 T02 (2007.84 - 2205.74) / 62.98 = -3.142.
- `nan` 값이 올 수 있고, `reason`에 `HAND_OFF_LONG`, `HAND_LIFTED` 같은 사유가 들어간다.
- 경고 비트: 1 `LOW_SAMPLES`, 2 `LOW_IR`, 4 `FSR_SAT`, 8 `GSR_SAT`, 16 `CONTACT_DIP`, 32 `PPG_OVERFLOW`. 실측에 목록에 없는 비트 64가 나온다 (I13).
- 헤더 줄(`#SUMMARY_HEADER`, `#BASELINE_HEADER`)은 부팅 때만 온다. 없으면 필드 수가 맞을 때 `KITEL_MAIN_V5` 고정 헤더로 해석한다 (collector와 같은 폴백).
- 기기가 크래시하면 `Guru Meditation Error` 레지스터 덤프 줄이 섞인다. 해석기는 이 줄을 해석 불가로 세고 넘어간다.

서버의 줄 해석기(`server/source/lines.py`)는 `collector/record_session.py`의 상수·함수(`FW_SAMPLE_HEADER`, `SUMMARY_HEADER_V5`, `BASELINE_HEADER_V5`, `WARN_BITS`, `warn_names`, `check_segment` 등)를 import해서 그대로 쓰고, 줄 판별 순서·폴백·필드 위치를 `Run`과 같게 둔다. `Run`은 해석하면서 파일을 쓰므로 직접 부르지 않는다. 실측 로그 4개에서 `recover_data.parse_raw_log`와 결과가 같고, 진행 추적기의 시도 상태가 collector `Run`과 같음을 테스트로 확인한다.

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

서버는 `protocol.csv`(라벨 포함)를 읽지 않는다.

### 5.3 서버 ↔ 웹 (WebSocket `/ws`, 보기 전용, 구현됨)

서버 → 웹 (JSON, nan 없음):

| type | 내용 |
|---|---|
| `device_status` | `input`(dummy/replay/serial), `input_label`, `live`, `source`(dummy/hardware), `connected`, `finished`, `message`, `port`, `replay_run` |
| `phase_update` | `phase`(단계 변화) 또는 `event`(손 접촉, 접촉 문제, 무효, 세션 시작/종료, 재부팅), `trial`, `esp_session`, `session_index`, `session_active` |
| `sample_batch` | 그래프용 원시 샘플. 열 단위 배열(`time_ms, fsr, gsr, ir, red, phase, trial, seq, contact`), 2개 중 1개(25Hz), 0.25초마다, 묶음당 최대 100개(`dropped`에 버린 수) |
| `baseline_result` | 기준 측정 평균/표준편차, 상태, 경고, 표준편차 하한 적용 여부 |
| `bpm_update` | 심박 추정값 `bpm`(없으면 null), `estimate: true`, `reason`, `reason_text`, 1초마다 |
| `question_update` | 시도에 배정한 질문: `session_index`, `trial`, `question_id`, `question_no`, `question_text`, `question_label`(연습 질문 / 본 질문 n/5 / 추가 질문 n), `role`, `exhausted`, `order_note`, `seed`. READY START에 배정하고 단계 메시지보다 먼저 보낸다. 세션이 끝나면 `question_text=null`로 비운다. 정답 라벨 키는 없다 (`label` 대신 `question_label`) |
| `trial_result` | PRD 9절 형식 + `judge_note`, `disclaimer`, `hold_code`, `device_status`, `question_id`, `question_text`, `question_label`. 서버 전용 항목(`server_only`, 예: 알 수 없는 경고 비트)은 빼고 보낸다 |
| `error` | `retryable`, 사용자용 `message` |

웹 → 서버: **미구현.** 받은 메시지는 모두 무시한다 (보기 전용). 예전 설계의 `set_mode`, `new_session`은 만들지 않았다. 입력원은 서버 환경 변수로만 정한다.

### 5.4 저장 규칙

- `source`: `dummy` 또는 `hardware`로 고정 (재생과 시리얼은 `hardware`)
- 필수 키: `participant_id`, `session_id`, `trial_id`, 기기 시각(`time_ms`), 서버 수신 시각
- 원시 IR/Red/GSR/FSR 값과 품질 플래그를 보관한다.
- `ground_truth`는 실험 기록에만 둔다.
- 학습되지 않은 상태에서는 `model_version`, `score`를 null로 둔다.
- **현재 서버는 기록을 저장하지 않는다 (I6).** 서버 전용 경고는 서버 콘솔 로그와 명령줄 출력에만 남는다. 데이터 수집은 수집기를 쓴다. 웹의 기록/내보내기 페이지는 예전 데모 기록만 다룬다.

## 6. 판정기 인터페이스

```python
# server/judges/base.py
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
    contributions: list = field(default_factory=list)
    hold_reason: Optional[str] = None   # 보류 코드 (문장은 reasons.py)

class Judge(Protocol):
    name: str
    version: str
    trained: bool
    def judge(self, features: dict, baseline: dict, quality: dict) -> Verdict: ...
```

입력(중첩 포함)에 라벨 키(`ground_truth`, `condition`, `planned_condition`, `marked_lie`, `label`, `confirmed`)가 있으면 `ValueError`를 낸다.

구현 상태:

1. `DummyJudge`: **만들지 않았다.** 사용자 결정으로 고정 가짜 계수 대신 RuleJudge를 먼저 만들었다 (2026-11-04).
2. `RuleJudge` (`server/judges/rule.py`, **구현됨**): `name="rule"`, `version="0.0.1"`, `trained=False`, `score=None`.
   - 입력 특징은 **국소 dz**(`fsr_local_dz`, `gsr_local_dz`)뿐이다. 기기 dz(`fsr_dz`, `gsr_dz`)는 표시용으로만 함께 보낸다.
   - |국소 dz| ≥ 임계값인 센서가 `MIN_SENSORS_OVER`개 이상이면 `lie`, 아니면 `truth`. 품질 보류, 특징 nan, 임계값 미설정(None)이면 `inconclusive`.
   - 임계값: `FSR_LOCAL_DZ_THRESHOLD = 3.0`, `GSR_LOCAL_DZ_THRESHOLD = 3.0`, `MIN_SENSORS_OVER = 1`. **통계에서 흔히 쓰는 관례(3σ)로 정한 값이며 라벨 데이터로 맞추거나 검증하지 않았다.** 이 안내를 상수 주석과 화면(`judge_note`)에 둔다.
   - 기여도 = |값| / 임계값 (1 이상이면 임계값을 넘음). 이유 문장은 실제로 임계값을 넘은 센서만 말한다.
   - 임계값은 설정 파일이 아니라 코드 상수다 (M5의 "설정 파일"은 미구현).
3. `LogregJudge`: **미구현 (M6).** `server/models/model.json`(특징 순서, 정규화 값, 계수, 절편, 임계값, 필터 설정, 버전)을 읽게 한다. 기획서대로 로지스틱 회귀.

판정기 선택: `JUDGE=rule` (현재 rule만 허용).

## 7. 전처리와 특징 추출

전처리 시작값은 `collector/analyze_data.py`를 따른다 (원시 샘플 필터링 특징은 아직 미구현).

| 센서 | 처리 |
|---|---|
| FSR | 5샘플 중앙값 + 5Hz 저역통과 |
| GSR | 5샘플 중앙값 + 1Hz 저역통과 |
| PPG | 0.5~5Hz 대역통과 (초기 실험용 범위). 심박 추정은 40~200 BPM(0.67~3.33Hz) 대역통과 |

실시간 경로에서는 인과적 필터만 쓴다 (`sosfilt`). `filtfilt`는 분석용이다. 샘플링 속도는 기기 시각으로 추정한다 (`fs = (샘플 수 - 1) / 측정 시간`).

### 7.1 구현된 특징: 국소 dz (`server/pipeline/features.py`)

국소 dz = (같은 시도의 RESPONSE 평균 − QUESTION 평균) / max(기기 요약의 `bl_*_sd`, 하한)

- 하한: `FSR_SD_FLOOR = 20`, `GSR_SD_FLOOR = 5` (ADC 단위, 임시값). `MIN_QUESTION_SAMPLES = 50` (1초). 짧으면 보류.
- 왜: 기기 dz는 세션이 길어질수록 한 방향으로 커지는 추세가 있었다 (161545의 유효 시도 10개가 3.0σ 기준으로 모두 거짓이 되는 수준). 국소 dz는 바로 앞의 질문 구간을 기준으로 삼아 이 추세를 **줄이지만 없애지는 못한다.** 실측 4개 로그에서 기울기 절대값은 12칸 중 11칸, 범위는 12칸 중 10칸에서 줄었지만, 국소 GSR은 시도 순서와의 상관 |r| 0.60~0.99로 방향성이 남는다.
- 실측 유효 시도 35개의 국소 |dz| 중앙값: FSR 0.93, GSR 0.77 (기기 dz는 3.69, 4.03). FSR의 큰 값 5개 중 2개는 `CONTACT_SHIFT`, 3개는 구간 안의 큰 압력 변동이었다.
- 질문 구간이 좋은 기준인지는 모른다 (질문을 듣는 동안의 긴장이 섞일 수 있음).

### 7.2 최소 품질 검사 (`server/pipeline/quality.py`)

보류 코드(앞에서부터): `DEVICE_RESET`(시도 중 재부팅), `NO_SUMMARY`, `DEVICE_INVALID:<사유>`, `RAW_*`(원시 샘플, 아래 표), `Q_TOO_SHORT`, `NO_RESPONSE`, `BL_SD_MISSING`, `CONTACT_EVENT:<이벤트>`.

원시 샘플 검사(S10): 시도마다 collector `check_segment`를 그대로 돌리고 결과를 나눈다. 판정 특징(국소 dz)이 질문·답변 샘플만 쓰므로 접촉·포화는 그 구간에 있을 때만 보류한다.

| check_segment 코드 | 뜻 | 처리 |
|---|---|---|
| `NO_SAMPLES` | 샘플 없음 | 보류 `RAW_NO_SAMPLES` |
| `DUP_TS`, `BACKWARDS_TS` | 같은 시각이 두 번 / 시각이 거꾸로 | 보류 `RAW_TIME_ERROR` |
| `SERIAL_LOST` | 순번이 건너뜀 (전송 중 샘플 빠짐) | 2%(`MAX_LOST_RATIO`, 임시값) 초과면 보류 `RAW_SAMPLES_LOST`, 이하면 경고 |
| `RESPONSE_FEW_SAMPLES` | 답변 샘플이 기대의 90% 미만 | 보류 `RAW_FEW_SAMPLES` |
| `PHASE_ORDER` | 단계 순서가 READY→QUESTION→RESPONSE가 아님 | 보류 `RAW_PHASE_ORDER` |
| `NO_CONTACT_SAMPLES` | contact=0 | 질문·답변 구간이면 보류 `RAW_NO_CONTACT`, 준비 구간만이면 경고 |
| `FSR_4095`, `GSR_SAT` | ADC 상한/하한 | 질문·답변 구간이면 보류 `RAW_FSR_SAT`/`RAW_GSR_SAT`, 준비 구간만이면 경고 |
| `TIME_GAPS` | 간격 30ms 초과 (실측 36~40ms, 모든 시도) | 경고 |
| `RATE`, `RESPONSE_DURATION`, `PHASE_MISMATCH` | 속도 45~55Hz 밖 / 답변 20초±50ms 밖 / 단계 표시와 시각 어긋남 | 경고 |

- 실측 4개 로그 38개 시도에서 이 변경 전후의 판정과 `hold_code`는 모두 같다 (2026-11-04 비교). 판정된 시도에는 `TIME_GAPS`만 나오고, 다른 코드는 이미 기기 무효로 보류된 시도에만 나왔다.
- `trial_result.quality`에 `warning_texts`(화면에 작게 보여 줄 쉬운 문장)와 `raw_check`(검사 수치)를 더했다. 화면은 "참고 (판정에는 영향 없음)"로 작게 보여 준다. 실측 시도는 모두 "신호 간격이 가끔 조금 늦었어요."가 붙는다.

- `HOLD_ON_CONTACT_EVENTS = True`: QUESTION 시작~RESPONSE 끝 사이에 `CONTACT_SHIFT`, `CONTACT_DIP`, `HAND_LOST`, `BASELINE_LOST`가 있으면 보류한다. READY나 RESULT 단계의 접촉 변화는 보류하지 않는다.
- 경고 비트는 경고로만 남긴다. 알 수 없는 비트(`UNKNOWN_WARN_BITS`)는 화면에 보이지 않는다.
- 화면 문장은 쉬운 말로 쓴다 (예: "손 위치가 바뀌어 이 답변은 측정에서 제외했어요."). 코드는 `hold_code`에 따로 남는다.
- 기기 무효 시도에 원시 문제가 같이 있으면 `hold_code`는 기기 사유가 먼저다 (원시 코드는 `flags`에 남는다).

### 7.3 심박 추정값 (`server/pipeline/bpm.py`, 화면 표시용)

- 최근 12초(앞 2초는 필터 안정용) 중 마지막 10초, 40~200 BPM 대역통과, 맥박 방향 2가지 × 피크 돌출도 기준 2가지 후보 중 간격이 고른(변동계수 ≤ 0.15) 후보 가운데 피크가 가장 많은 것, 평균 간격으로 BPM.
- 값을 내지 않는 경우(화면 "—"와 이유): 신호를 모으는 중, 손 접촉 없음, 센서 신호가 약함(IR 중앙값 < 50,000), 맥박 신호가 약함(필터 후 표준편차 < 20), 맥박을 충분히 찾지 못함, 맥박 간격이 불규칙함, 범위를 벗어난 값, 측정 구간이 아님.
- 기준값은 임시값이다. 신호 크기 기준은 실측 로그의 크기로, 변동계수 기준은 합성 신호 시험(0.25에서는 60개 중 10개 오차, 0.15에서는 0개)으로 정했다. **정답 심박과 비교하지 않았다 (I14).**
- 합성 신호(잡음, 호흡성 흔들림, 둘째 봉우리 포함)에서 50~150 BPM을 ±2 이내로 맞히고, 맥박 없는 신호에서는 값을 내지 않음을 테스트로 확인한다.
- 실측 161545: 1초마다 307회 중 값 97회(57.3~80.6 BPM, 중앙값 72.2), 불규칙 73회, 신호를 모으는 중 132회. 시도마다 12초가 다시 모여야 하므로 값은 주로 질문 후반과 답변 구간에 나온다.
- **판정(RuleJudge)에 쓰지 않는다.** `hr_delta` 같은 심박 특징은 정답 비교와 라벨 데이터가 생긴 뒤 검토한다.

### 7.4 전체 특징 (S11, 학습용, 판정에 쓰지 않음)

`server/pipeline/features.py`의 `extended_features(시도 샘플, 기준 측정 샘플)`. RuleJudge에는 기존 4개 특징(국소 dz, 기기 dz)만 넘기고, 전체 특징은 `trial_result.features`와 `feature_info`, `python -m server.cli features` CSV에만 담는다 (판정기 입력을 가로채 테스트로 확인).

**전처리** (`server/pipeline/signal.py`, 인과적): FSR 과거 5샘플 중앙값 + 5Hz 2차 저역통과, GSR 과거 5샘플 중앙값 + 1Hz 2차 저역통과, PPG(IR) 0.5~5Hz 2차 대역통과. `sosfilt`만 쓰고 시작 상태는 첫 샘플 값에 맞춘다(`sosfilt_zi`). `filtfilt`는 쓰지 않는다 (호출 여부를 구문 분석으로 테스트). 샘플링 속도는 기기 시각으로 추정한다. 시도는 READY~RESPONSE를 한 번에, 기준 측정은 따로 필터한다.

**구간**

- 답변(R): 시도의 RESPONSE 샘플. 5초(`MIN_RESPONSE_SEC`) 미만이면 모든 특징 nan.
- 기준 측정(bl): 그 시도가 쓴 기준 측정 구간(진행 추적기에서 같은 세션, 같은 `baseline_id`로 찾음, `tracker.baseline_for`). 앞 2초(`BASELINE_SKIP_SEC`)를 버리고, 남은 구간이 5초 미만이거나 찾지 못하면 bl 특징은 nan. 기기 요약의 평균/표준편차로 대신하지 않는다 (필터 전 값이라 섞으면 기준이 달라짐). 실측 38개 시도는 모두 찾았다.
- 질문(q): 같은 시도의 QUESTION 샘플. 50개(1초) 미만이면 q 특징 nan.

**정의** (s = fsr, gsr; ref = bl, q; m, sd = 기준 구간의 평균, 표준편차; sdu = max(sd, 하한 FSR 20 / GSR 5))

| 특징 | 정의 | 단위·범위 |
|---|---|---|
| `{s}_mean_delta_{ref}_z` | (답변 평균 − m) / sdu | σ, ±10 클리핑 |
| `{s}_max_change_{ref}_z` | max\|답변 − m\| / sdu | σ, 0~10 클리핑 |
| `{s}_sd_ratio_{ref}` | 답변 표준편차 / sdu. 답변 시작의 변화(계단)도 구간 안에 남아 값을 키운다 | 배, 0~10 클리핑 |
| `{s}_duration_{ref}_s` | 답변 중 \|답변 − m\| / sdu > 2.0인 샘플 수 / fs | 초 |
| `hr_resp`, `hr_bl`, `hr_q` | `bpm.estimate_bpm`(7.3절) 재사용: 답변 끝 10초(답변 8초 이상일 때), 기준 측정 끝 10초, 질문 끝까지 이어진 12초 | BPM |
| `hr_delta_bl`, `hr_delta_q` | hr_resp − hr_bl, hr_resp − hr_q | BPM |
| `ppg_amp_ratio_{ref}` | 답변의 대역통과 IR 표준편차 / 기준의 표준편차 (기준 진폭 20 미만이면 nan) | 배, 0~10 클리핑 |

- 평평한 신호: 기준 표준편차가 0.5(`FLAT_SD`) 미만이면 그 센서·기준의 특징을 nan으로 거부하고 `feature_info.flat`에 적는다.
- 모든 상수(`BASELINE_SKIP_SEC`, `MIN_BASELINE_SEC`, `MIN_RESPONSE_SEC`, `FLAT_SD`, `Z_CLIP`, `RATIO_CLIP`, `DURATION_Z`, `PPG_AMP_FLOOR`)는 **임시값**이다. 라벨 데이터로 맞추거나 검증하지 않았다.
- nan-safe(계산할 수 없으면 nan, JSON에서는 null), 결정적(난수 없음), 라벨 비참조(입력은 샘플뿐)를 테스트로 확인한다.
- 실측 4개 로그(38개 시도, 정답 비교 아님): 판정된 시도 32개에서 센서 특징과 맥파 진폭 비율은 nan 0%. `hr_resp` nan 22%, `hr_bl` 12%, `hr_q` 91%(질문 끝까지 12초가 이어지는 경우가 드묾). 클리핑 경계(10)에 닿은 비율이 높다: `*_max_change_*` 9~14개, `*_mean_delta_bl_z` 9~10개 (기준 측정 기반은 기기 dz처럼 시간 추세의 영향을 받는 것으로 보임). 학습 전에 클리핑과 하한을 다시 정해야 한다.
- 이 변경 전후로 실측 판정 분포는 같다 (161545: 진실 5, 거짓 2, 보류 4 등, 38개 시도 모두 판정과 `hold_code` 동일).
- PRD 9절 "공통"의 기준 평균/중앙값/범위 자체는 특징으로 넣지 않았다 (시도 간 변화가 아니라 사람별 수준이라 참가자 분리 검증에서 누수 위험).

학습 시 스케일링, 특징 선택, 하이퍼파라미터 조정은 학습 폴드 안에서만 한다.

### 7.5 학습 파이프라인 (S12, `training/`)

**데이터셋** (`training/build_dataset.py`): `data/real/experiments/run_*`를 읽기만 한다.

- 특징: `raw_serial_log.txt` → 진행 추적기 → `server.result.judge_trial`(기준 측정 구간 포함) → `features`. 서버와 같은 코드라 학습/서빙 불일치가 없다 (테스트로 같은 값인지 확인). 학습 특징은 국소 dz 2개 + 전체 특징 23개. 기기 dz는 세션 추세 때문에 넣지 않는다. RuleJudge 판정도 기준 모델 비교용으로 담는다.
- 라벨 (2026-11-04 사용자 확인): 세션 폴더 `trials_summary.csv`의 `confirmed`가 `as_planned` 또는 `corrected`이고 `condition`이 `truth`/`lie`인 시도만 쓴다 (truth=0, lie=1). `planned_condition`만 있는 시도와 `condition=test`는 쓰지 않는다. `protocol.csv`의 `asked_trials`/`final_condition`과 교차 확인해 다르거나 없으면 `label_mismatch`로 제외한다. `protocol.csv`가 없는 예전 형식 세션은 제외한다.
- 조인 키: (run 폴더, 세션 순번 k, 기기 시도 번호). `run_events.csv`의 k번째 `SESSION START` 줄의 `session_id` = 세션 폴더 (없으면 폴더 이름의 `_S{k:02d}_`). 참가자는 `trials_summary.csv`의 `participant_id`.
- 제외 사유(먼저 걸린 하나): 더미, 세션 기록 폴더 없음, 예전 형식, 기록에 시도 없음, 연습, 계획 외, 보류(품질), 미확정, test, 진실/거짓 아님, protocol 불일치. 사유별 개수를 출력한다.
- 더미 판별: 경로에 `dummy` 폴더가 있거나 run 폴더의 `SOURCE` 파일 내용이 `dummy`. 학습 단계에서도 `source`가 hardware가 아닌 행이 있으면 거부한다.
- 실측(2026-11-04): run 12개, 시도 41개 중 0건 (연습 38, 예전 형식 3) → "학습할 데이터가 없습니다"로 정상 종료.

**학습** (`training/train_logreg.py`)

- 검증: 참가자 단위 분리. 10명 이하면 한 명씩 빼기, 많으면 GroupKFold(5). 결측 대체(중앙값), 특징 선택(결측 50% 초과·분산 0 제외), 표준화, ±5 클리핑, C 선택(안쪽 참가자 분리 검증, C ∈ {0.01, 0.1, 1, 10})은 모두 학습 폴드 안에서만 한다. 테스트 폴드는 변환만 한다 (테스트 참가자에 아주 큰 값을 넣어 표준화에 섞이지 않는지 확인).
- 모델: L2 로지스틱 회귀, `class_weight=balanced`, 임계값 0.5.
- 기준 모델: 항상 다수 클래스(학습 폴드 기준), RuleJudge(3.0σ). 지표: 균형 정확도, 거짓 검출률, 진실→거짓 오판율, 혼동행렬, 참가자별 시도 수.
- 안전장치: 참가자 5명 미만이면 "탐색용 결과이며 채택하지 않습니다"를 크게 출력하고 model.json을 만들지 않는다 (`--allow-small`이면 `exploratory: true`로만 만든다). 로지스틱 회귀의 균형 정확도가 두 기준 모델보다 모두 높지 않으면 이유를 출력하고 만들지 않는다.
- `model.json`: 특징 순서, 결측 대체값, 정규화 평균/표준편차, 클리핑 범위, 계수, 절편, 임계값, C, 필터·특징 설정, 버전, 학습 참가자 수·시도 수, 검증 지표. 원시 데이터, 참가자 ID, run 이름은 넣지 않는다. 기본 출력 `training/out/`(gitignore), `data/real/`·`collector/` 안은 거부.
- 임시값: `MIN_PARTICIPANTS=5`, `MAX_MISSING_FRAC=0.5`, `STD_CLIP=5`, `C_GRID`, 임계값 0.5. 라벨 데이터로 정하지 않았다.
- 가짜 데이터: 테스트는 `tmp_path`에 만든 가짜 collector 구조와 가짜 라벨, 합성 특징 표만 쓴다. 학습 코드가 읽는 기본 경로(`data/real/experiments`)와 분리되어 있다.
- 아직 없음: `model.json`을 읽어 판정하는 `LogregJudge` (M6 나머지).

## 8. 웹 (web/, 구현됨)

- 기존 `Truthpulse_Game` 화면을 그대로 쓴다. `/ws`에 처음 연결되면 기기 연동 모드로 바뀐다. 연결할 수 없으면(예전 데모 서버) 기존 데모 모드로 동작한다.
- 기기 연동 모드에서는 `demoSample` 공급과 `summarizeDemo`를 멈추고, 세션 제어 버튼과 정답 입력칸을 숨기고, 기기 단계를 따라가는 표시로 바꾼다.
- 표시: 입력원 이름(재생/더미/실시간 기기 구분), 세션 순번, 단계, 그래프(채널마다 정규화한 상대 크기), GSR/FSR 기준값, PPG 카드의 심박 추정값("BPM 추정값", 값이 없을 때만 "심박 계산 전"과 이유), 결과 카드(진실/거짓/보류, "모델 미학습(데모)" 배지, 판정 이유 또는 보류 사유, 센서별 막대(가운데 선 = 임계값), `judge_note`, `disclaimer`), 최근 판정 3개.
- 재생·더미 화면에서는 "실시간"을 표시하지 않고, 손 올리기 단계에서는 "측정은 저장된 로그를 따라갑니다."(더미: "측정은 합성 신호를 따라갑니다.")로 바꾼다.
- 연결이 끊기면 주황색 띠를 띄우고 1초부터 최대 5초 간격으로 자동 재연결한다. 재연결하면 서버가 상태와 기록을 다시 보내고, 같은 결과는 한 번만 표시한다.
- 질문 문장: READY 단계에서는 다음 질문을 흐리게 미리 보여 주고(진행자 준비용), QUESTION 단계에서 크게, 그 시도의 답변·분석·결과 단계에서는 흐리게, 손 올리기·기준 측정·세션 종료와 다른 시도에서는 숨긴다. 안내 문구: 재생 "기록 당시 실제로 물은 질문과 다를 수 있습니다", 기기·더미 "질문 순서는 서버가 정합니다. 진행자는 화면의 질문을 읽어 주세요." 결과 카드와 최근 판정에도 질문 문장을 보여 준다.
- 기기 연동 모드의 결과는 브라우저 기록 페이지에 저장하지 않는다 (기록/내보내기는 데모 기록만).
- 브라우저 화면 확인은 사람이 한다 (자동 브라우저 테스트 없음).

### 8.1 질문 순서 (`server/questions.py`, 게임 모드)

- 기기는 질문 문장을 보내지 않으므로 서버가 정한다. 질문 은행은 collector의 `load_bank`로 읽고(`no, item_id, question_text`만), 다른 열은 읽지 않는다. 정답 라벨(거짓으로 답할 질문)은 서버가 모르며 다루지 않는다. 판정 코드는 질문 모듈을 쓰지 않는다 (테스트로 확인).
- 세션마다 연습 1(무작위) + 본 질문 5(섞기) + 추가 질문 9(섞기). 난수는 `QUESTION_SEED`와 세션 순번으로 정한다. collector의 `Protocol`은 참가자의 거짓 표시(라벨)가 있어야 하므로 쓰지 않고, 실험 모드(collector)의 질문 순서와는 다르다.
- 배정: 시도가 열릴 때(READY START) 대기열에서 하나를 꺼낸다 (기기 시도 번호로 계산하지 않음). 완료되지 못한 시도는 질문 구간(QUESTION) 전에 끝났으면 같은 질문을 다시 쓰고, 이후에 끝났으면 다음 질문으로 넘어간다 (사용자 결정, 2026-11-04).
- 기기 시도 번호와 어긋날 수 있는 경우와 처리:

| 경우 | 처리 |
|---|---|
| 무효(ABORTED) 뒤에도 기기 시도 번호가 계속 오름 | 번호가 아니라 READY START마다 배정하므로 어긋나지 않음 |
| 질문 전에 무효 (161545 S01 T01, READY 중 손 뗌) | 다음 시도에 같은 질문 |
| 질문 이후 무효 (답변 중 손 뗌 등) | 다음 질문 |
| INCOMPLETE (결과 없이 다음 시도) | 위 두 규칙과 같음 |
| 시도 중 재부팅 | 세션 종료(INTERRUPTED), 다음 세션은 새 순서 |
| 세션 재시작 | 새 순서 |
| 세션 도중에 서버를 켬 (I12) | 그 세션은 질문 없음, 다음 세션부터 정상 |
| 시리얼 끊김 동안 READY START를 놓침 | 그 시도는 질문 없음 |
| 15문항을 모두 씀 | "준비된 질문을 모두 사용했습니다" |
| 재생 | 기록 당시 실제로 물은 질문(라벨이 든 `protocol.csv`에만 있음)과 다름. 화면에 안내 |

## 9. 펌웨어 (firmware/)

- 펌웨어 소스(KITEL_MAIN_V5)는 현재 저장소에 없다. 추가한 뒤 작업한다 (I3).
- 확인할 것: LED/OLED 판정·표시 기준, 연결 방식, `ANALYZING`/`RESULT` 단계의 동작, 재부팅 원인(I7), 경고 비트 64의 뜻(I13), DTR/RTS 동작(I11), 버튼 디바운스.
- 샘플 출력 형식(5.1절)은 바꾸지 않는다.
- 기기 판정 결과를 PC로 보내려면 줄 형식을 새로 정해야 한다 (F11, F12). 펌웨어 담당자와 협의한 뒤 정한다.

## 10. 테스트

| 대상 | 방법 | 파일 |
|---|---|---|
| 환경 | 파이썬 버전, 패키지 임포트, pandas<3 | `tests/test_env.py` |
| 수집기 | 저장된 로그 재생 (직접 실행) | `collector\test_replay.py` |
| 줄 해석 | 샘플, `#EVENT`, `#BASELINE`, `#SUMMARY`(헤더/폴백/불일치), `nan`, 실측 4개를 `parse_raw_log`와 비교 | `tests/test_lines.py` |
| 진행 추적 | 단계 순서, 실측 4개와 더미를 collector `Run`과 비교, 재부팅/INCOMPLETE | `tests/test_tracker.py` |
| 입력원 | 재생 속도, 더미 형식·결정성·dz 식 | `tests/test_sources.py` |
| 시리얼 | 가짜 시리얼: 줄 나누기, DTR/RTS 끄고 열기, 쓰기 0번, 끊김·재연결, 무신호, 재부팅 시 INTERRUPTED, 포트 목록 | `tests/test_serial_source.py` |
| 특징, 품질 | 국소 dz 값과 하한, 짧은 질문, 실측 값 일치, 접촉 이벤트 정책, 경고 비트, 원시 샘플 코드별 보류/경고(collector `check_segment` 결과와 일치) | `tests/test_features.py`, `tests/test_quality.py` |
| 라벨 독립성 | 라벨 키 거부, 라벨을 바꿔도 특징과 판정이 같음 | `tests/test_judges.py`, `tests/test_features.py` |
| 판정기, 이유 | 임계값 경계, 보류, 넘은 센서만 언급, 쉬운 보류 문장, 실측 161545 회귀 | `tests/test_judges.py`, `tests/test_reasons.py` |
| 심박 | 합성 50~150 BPM ±2, 신호 없음/약함/불규칙은 None, 판정 미사용, 실측 범위 | `tests/test_bpm.py` |
| 전처리, 전체 특징 | 인과성(뒤쪽 샘플을 바꿔도 앞 출력 동일), filtfilt 미사용, 합성 신호로 각 특징 값, 평평/기준 없음/짧은 답변 nan, 클리핑, 결정성, 라벨 비참조, RuleJudge 입력 불변, 실측 nan 비율, CSV 출력 보호 | `tests/test_signal.py`, `tests/test_features_full.py` |
| 학습 | 라벨 규칙과 제외 사유, 서버와 같은 특징, 라벨을 바꿔도 특징 동일, 더미 거부(폴더·SOURCE·행), 예전 형식, 0건 정상 종료, 참가자 분리, 테스트 폴드 비노출, 5명 미만 경고, 기준 미달 시 내보내기 거부, model.json 내용과 서빙 일치, 출력 보호, 결정성 | `tests/test_training.py` |
| 질문 | 은행 라벨 열 무시, 순서·시드, 배정 규칙(질문 전/후 무효), 세션·재부팅, 소진, 실측 161545, 판정 미사용 | `tests/test_questions.py` |
| 서버 | 임시 uvicorn + websockets: 방송, 새 접속자 재전송, 샘플·심박 전송 간격, 재생 결과, 오류 | `tests/test_app.py`, `tests/test_stream.py` |
| 웹 | 입력원 문구("실시간" 금지), 판정 화면 모델, 재연결, 세션 표시, 심박 표시 | `web/tests/live.test.mjs` (+ 기존 `core.test.mjs`) |
| 통합 | 사람이 브라우저로 확인 (3.5절). 더미 10회 연속, 재생, 실제 기기 | — |

## 11. 알려진 이슈와 한계

| 번호 | 내용 | 상태 |
|---|---|---|
| I1 | 기록 당시(2026-11-03) 수집기가 ESP32의 `#SUMMARY`/`#BASELINE`(valid=1)을 시도에 붙이지 못해 정상 시도 38개가 모두 `ABORTED`가 되었고 연습 질문만 반복됐다. TIME_GAPS 가설은 틀렸다(무결성 표시에만 영향). **현재 `record_session.py`로 같은 로그를 재생하면 35개 COMPLETE, 진짜 무효 3개만 ABORTED**(2026-11-04 재생 시험). 기록 당시 코드 버전이 없어 당시 원인은 확인하지 못했다 (헤더 폴백이 없었을 것으로 추정). 기존 기록은 모두 연습 질문이라 학습 가능 데이터는 여전히 0건. | 현재 코드에서 재현 안 됨. 짧은 실제 세션으로 본 질문 라벨이 남는지 확인 필요 |
| I2 | 기기 dz가 -32처럼 매우 크고 세션이 길어질수록 한 방향으로 커진다. 판정은 국소 dz와 표준편차 하한으로 완화했지만 국소 GSR의 방향성은 남는다 (7.1절). 클리핑은 화면 막대에서만 한다. | 완화됨, 원인 미확인 |
| I3 | 펌웨어 소스가 저장소에 없다. | 미해결 |
| I4 | 웹의 심박 채널: 서버가 IR로 심박을 추정해 보낸다 (7.3절). | 구현됨 (추정값, 미검증) |
| I6 | 서버가 받은 기록(특히 serial)을 저장하지 않는다. 저장 위치(`data/real/`은 읽기 전용, `data/dummy/`와 섞으면 안 됨)와 형식을 정해야 한다. | 미해결 |
| I7 | 실험 로그(`run_20261103_154858`)에 ESP32 크래시(`Guru Meditation Error: Core 1 panic'ed (LoadProhibited)`)와 레지스터 덤프 21줄이 있다. 해석기는 넘어간다. | 펌웨어 확인 필요 |
| I8 | `collector/test_replay.py`가 `experiments/`를 현재 폴더 기준으로 찾아서, 저장소 루트에서 실행하면 T8, T9가 건너뛰어진다. | 미해결 (collector 수정 필요, 확인 후 진행) |
| I9 | 기기가 LED/OLED 참/거짓을 어떻게 판정하는지 모른다. 연결이 USB인지 블루투스(SPP)인지 문서로 확인되지 않았다 (COM 포트 읽기는 사용자가 실기기로 확인). | 펌웨어 확인 필요 |
| I10 | 샘플 간격이 36~40ms로 늘어나는 경우가 시도마다 있어 모든 시도가 `integrity=CHECK`다. 원인 미확인. | 미해결 (경고로만 둠) |
| I11 | 서버가 DTR/RTS를 끄고 포트를 열 때 실제 보드가 재부팅되지 않는지 확인 기록이 없다. | 확인 필요 |
| I12 | 세션 도중에 서버를 켜면 그 세션의 `SESSION START`를 놓쳐 다음 세션까지 그래프·판정이 없다 (collector와 같은 규칙). | 한계 (운영으로 회피: 서버 먼저 켜기) |
| I13 | 경고 비트 64가 `WARN_BITS`에 없다 (161545 T5, T6). 화면에는 숨기고 서버 로그에만 남긴다. | 펌웨어 확인 필요 |
| I14 | 심박 추정값을 정답 심박(심전도, 다른 측정기)과 비교한 적이 없다. 정확도는 모른다. | 미검증 |
| I15 | RuleJudge 임계값 3.0σ는 관례값이며 라벨로 검증하지 않았다. 실측 161545에서 진실 5, 거짓 2, 보류 4가 나오지만 맞았는지는 알 수 없다. | 미검증 (라벨 데이터 필요) |
| I16 | 더미 입력원의 일부 흐름(답변 중 손 뗌의 이벤트 순서, 보류 직후 세션 종료 버튼, 표준편차 식)은 실측으로 확인하지 않은 가정이다. | 한계 |
| I17 | 게임 모드 질문 문장 표시는 구현했다 (8.1절, 서버가 정한 순서). 참가자 별칭 입력은 없다 (웹에서 서버로 보내는 메시지 없음, `participant_id`는 null). | 질문 표시 구현됨, 별칭 미구현 |

## 12. 단계 체크리스트

완료하면 `[x]`로 바꾸고 날짜를 적는다.

- [x] **M0** 저장소, 폴더 구조, `.gitignore`, 문서, 개발 환경(`.venv`, `requirements.txt`, `pytest.ini`) (2026-11-04)
- [ ] **M1** I1 진단 완료(현재 코드에서 재현 안 됨, 2026-11-04) → `test_replay.py` 통과 (I8 포함) → 짧은 실제 세션에서 본 질문 라벨이 남는지 확인 → 본격 수집 시작
- [x] **M2** 입력원(dummy/replay/serial), 줄 해석 재사용, 진행 추적, 최소 품질 검사, 국소 dz, RuleJudge, 이유 생성, 심박 추정값을 명령줄과 서버에서 확인 (2026-11-04)
  - [x] S1 줄 해석기와 `parse` 명령 (collector 해석 규칙 재사용)
  - [x] S2 재생·더미 입력원과 진행 추적기 (collector `Run`과 상태 일치)
  - [x] S3 국소 dz, 최소 품질 검사, RuleJudge(3.0σ 관례값), 이유 생성 (`DummyJudge` 대신)
  - [x] S4 FastAPI 서버와 `/ws` 방송, 새 접속자 재전송
  - [x] S5 웹 기기 연동 모드 (판정·이유·배지, 그래프 샘플, 자동 재연결, 쉬운 보류 문장, 세션 번호)
  - [x] S5b 읽기 전용 시리얼 입력원 (DTR/RTS 끔, 끊김 시 재연결, `ports` 명령)
  - [x] S7 IR 심박 추정값 표시 (판정 미사용)
  - [x] S8 문서 갱신
- [x] **M2-S9** 게임 모드 질문 문장 표시 (서버가 정한 순서, `QUESTION_SEED`, 라벨 없음, `question_id` 채움) (2026-11-04)
- [x] **M2-S10** 원시 샘플 품질 검사 연결 (`check_segment` → 보류 `RAW_*` / 경고, 쉬운 문장, 실측 판정 변화 없음) (2026-11-04)
- [x] **S11** 학습용 전체 특징 추출 (인과적 전처리, 23개 특징, `features` CSV, 판정 미사용, 실측 판정 변화 없음) (2026-11-04) — M6 준비
- [x] **M3** FastAPI + WebSocket, 웹 연결(보기 전용), 단계 표시, 배지 (2026-11-04, M2의 S4·S5로 진행)
- [ ] **M4** 펌웨어 소스 확인 후 기기 결과와 서버 판정 일치 방안 결정, I6 결정
- [ ] **M5** `RuleJudge` (시연 예비안), 임계값 설정 파일 — RuleJudge는 M2-S3에서 구현. 임계값은 코드 상수이며 설정 파일과 라벨 검증은 남음
- [ ] **M6** 학습 파이프라인(참가자 분리 검증), `model.json`, `LogregJudge` 교체 — 파이프라인과 model.json 내보내기는 S12에서 구현(2026-11-04). 라벨 확정 데이터 0건이라 학습은 아직 못 함. `LogregJudge`는 남음
- [ ] **M7** 통합 테스트, 리허설, (선택) ESP32 이식과 저주파 피드백

M1의 수집은 개발과 병행해서 계속한다. M6은 라벨이 확정된 실측 데이터가 충분히 모인 뒤에 한다 (제안: 참가자 8~10명 이상, 확정된 질문 100~200개 이상).

## 13. 앞으로 할 일

우선순위는 제안이며 사용자가 정한다.

1. **라벨 데이터 수집 (M1):** 짧은 실제 세션으로 본 질문 라벨(`condition`, `confirmed`)이 남는지 확인한 뒤 본격 수집. 수집기와 서버는 같은 포트를 동시에 쓸 수 없으므로 수집은 수집기로 한다.
2. **서버 기록 저장 (I6):** 저장 위치와 형식 결정 후 `server/storage.py` (모든 기록에 `source`).
3. **로지스틱 회귀 (M6):** 파이프라인은 S12 완료 (`python -m training.train_logreg`). 라벨 데이터가 모이면 실행하고, 기준을 넘으면 `LogregJudge`를 만든다.
4. **RuleJudge 임계값 검증:** 라벨 데이터로 3.0σ와 국소 dz의 타당성을 평가하고 설정 파일로 옮긴다 (M5).
5. **심박 추정 검증 (I14):** 다른 측정기와 비교, 그 뒤에 `hr_delta` 특징 검토.
6. **블루투스 SPP:** ESP32 Bluetooth 시리얼(SPP)로 연결하면 Windows에 COM 포트로 잡히므로 `SOURCE=serial`을 그대로 쓸 수 있을 것으로 예상한다 (미확인). DTR/RTS, 재연결, 지연을 실기기로 확인한다.
7. **버튼 디바운스 (펌웨어):** 로그의 `IGNORED_*` 버튼 이벤트와 POLL/IRQ 중복 여부를 펌웨어 소스와 함께 확인한다.
8. **펌웨어 확인 (I3, I7, I9, I11, I13):** LED/OLED 판정 기준, 크래시 원인, 경고 비트 64, DTR/RTS 동작.
9. **TIME_GAPS 원인 확인 (I10):** 원시 샘플 검사 연결은 S10에서 완료. 36~40ms 간격의 원인(펌웨어 루프, USB 전송 등)은 미확인.
10. **참가자 별칭 입력 (I17).** 질문 표시는 S9에서 구현.
11. **`collector/test_replay.py` 경로 문제 (I8):** collector 수정이 필요하므로 이유와 영향을 알리고 확인을 받은 뒤 진행.

## 14. Git과 문서 관리

- 커밋, 푸시, 풀은 사용자가 직접 한다 (1절 규칙). 기능마다 새 브랜치에서 작업하고, 테스트가 통과한 뒤에만 합친다. main에 직접 커밋하지 않는다.
- 커밋 메시지 형식 제안: `기능: 설명`, `수정: 설명`, `문서: 설명`
- 깃에 올리지 않을 것: `CLAUDE.md`, `.claude/`, `data/`, `.venv/`, `node_modules/`, `__pycache__/`, `.env`, `.pytest_cache/`, `.vscode/`
