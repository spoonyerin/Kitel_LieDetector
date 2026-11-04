"""서버 기록 저장 (S16, I6). 환경 변수 SAVE=1일 때만 쓴다 (기본 꺼짐).

저장 위치: data/server/<날짜시각>_<입력원>/   (예: data/server/20261104_153012_serial/)
    run_info.json         서버 실행 설정: 입력원, source, 판정기, 질문 시드, 시작·끝 시각, 학습 제외 표시
    trial_results.jsonl   시도 결과 한 줄에 하나 (trial_result 전체, 서버 전용 항목 server_only 포함)
    raw_serial_log.txt    시리얼 입력원일 때만. "PC시각<TAB>기기 줄" (collector raw_serial_log.txt와 같은 형식,
                          PC시각도 collector now_str과 같은 형식). 빈 줄은 시리얼 입력원이 버리므로 없다.

규칙
- data/real, data/dummy, collector 안에는 절대 쓰지 않는다. 폴더를 만들기 전에 실제 경로(realpath)로 검사한다.
- 모든 기록에 source(dummy|hardware)와 input(dummy|replay|serial)를 둘 다 남긴다.
- 재생(replay)과 더미(dummy) 기록은 학습에 쓰지 않는다: 폴더 이름에 입력원을 넣고, run_info와 모든 결과 줄에
  training_excluded=true와 이유를 적는다 (재생은 data/real 원본의 중복, 더미는 합성 신호).
- 정답 라벨은 서버가 모르므로 저장하지 않는다 (판정 입력과 같은 라벨 키 검사를 저장 전에도 한다).
- 디스크 쓰기 실패는 예외로 서버를 멈추지 않는다. error에 이유를 남기고 이후 쓰기를 멈춘다 (호출한 쪽이 화면에 알림).
"""

import datetime as dt
import json
import math
import os
from typing import Optional

from server.config import DEFAULT_SAVE_ROOT, REPO_ROOT  # noqa: F401 (DEFAULT_SAVE_ROOT: 기본 저장 위치)
from server.judges.base import check_no_labels
from server.source.lines import _rs   # collector now_str (PC시각 형식) 재사용

# 이 폴더들 안에는 쓰지 않는다 (data/real: 실측 원본, data/dummy: 더미 전용, collector: 수집기 코드)
PROTECTED_DIRS = {
    "data/real": os.path.join(REPO_ROOT, "data", "real"),
    "data/dummy": os.path.join(REPO_ROOT, "data", "dummy"),
    "collector": os.path.join(REPO_ROOT, "collector"),
}
RECORD_FORMAT = "truthpulse-server-record"
RECORD_VERSION = "0.1"
EXCLUDE_REASONS = {
    "replay": "저장된 로그 재생 기록입니다. 원본(data/real)과 중복되므로 학습에 쓰지 않습니다.",
    "dummy": "합성 신호(더미) 기록입니다. 학습에 쓰지 않습니다.",
}
LABEL_NOTE = "정답 라벨(진실/거짓)은 서버가 알지 못하므로 저장하지 않습니다."


class StorageError(Exception):
    """저장 위치가 금지된 경로이거나 폴더를 만들 수 없음."""


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def protected_reason(path: str) -> Optional[str]:
    """path가 보호 폴더 안(또는 그 자체)이면 그 이름, 아니면 None."""
    p = _real(path)
    for name, d in PROTECTED_DIRS.items():
        base = _real(d)
        if p == base or p.startswith(base + os.sep):
            return name
    return None


def check_save_root(root: str) -> str:
    bad = protected_reason(root)
    if bad:
        raise StorageError(f"기록 저장 위치가 {bad}/ 안입니다. 이 폴더에는 쓰지 않습니다: {root}")
    return os.path.abspath(root)


def clean(v):
    """JSON에 nan/inf를 넣지 않는다 (null로 바꾼다)."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    return v


def _dumps(obj) -> str:
    return json.dumps(clean(obj), ensure_ascii=False, allow_nan=False)


class ServerRecorder:
    """서버 실행 한 번의 기록 폴더. 입력원 스레드 한 곳에서만 부른다."""

    def __init__(self, root: str, input_kind: str, source: str, now=None):
        if input_kind not in ("dummy", "replay", "serial"):
            raise StorageError(f"알 수 없는 입력원: {input_kind}")
        if source not in ("dummy", "hardware"):
            raise StorageError(f"알 수 없는 source: {source}")
        root = check_save_root(root)
        self.input_kind, self.source = input_kind, source
        self.training_excluded = input_kind in EXCLUDE_REASONS
        self.started_at = (now or dt.datetime.now)()
        stamp = self.started_at.strftime("%Y%m%d_%H%M%S")
        base = os.path.join(root, f"{stamp}_{input_kind}")
        path, n = base, 1
        try:
            os.makedirs(root, exist_ok=True)
            while True:
                try:
                    os.mkdir(path)   # 같은 이름이 있으면 덮어쓰지 않고 _2, _3…
                    break
                except FileExistsError:
                    n += 1
                    path = f"{base}_{n}"
        except OSError as e:
            raise StorageError(f"기록 폴더를 만들 수 없습니다 ({root}): {e}") from e
        if protected_reason(path):   # 심볼릭 링크 등으로 보호 폴더에 닿는 경우까지 한 번 더 막는다
            raise StorageError(f"기록 폴더가 보호 폴더 안입니다: {path}")
        self.dir = path
        self.error: Optional[str] = None
        self.n_results = 0
        self.n_raw = 0
        self.info: dict = {}
        self._raw = None

    # ---------- 공통 ----------
    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def rel_dir(self) -> str:
        """저장소 안이면 상대 경로(data/server/…), 밖이면 절대 경로."""
        try:
            rel = os.path.relpath(self.dir, REPO_ROOT)
        except ValueError:   # 다른 드라이브
            return self.dir
        return self.dir if rel.startswith("..") else rel.replace(os.sep, "/")

    def _fail(self, what: str, e: Exception) -> bool:
        if self.error is None:
            self.error = f"기록 저장에 실패해 저장을 멈췄습니다 ({what}): {e}. 판정과 화면 표시는 계속됩니다."
        self._close_raw()
        return False

    def _append(self, name: str, text: str) -> None:
        with open(os.path.join(self.dir, name), "a", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def _marks(self) -> dict:
        return {"source": self.source, "input": self.input_kind, "training_excluded": self.training_excluded,
                "training_excluded_reason": EXCLUDE_REASONS.get(self.input_kind)}

    # ---------- 실행 설정 ----------
    def write_info(self, **info) -> bool:
        """run_info.json을 (다시) 쓴다. info는 이전 값에 합친다."""
        if not self.ok:
            return False
        self.info.update(info)
        data = {"format": RECORD_FORMAT, "version": RECORD_VERSION, **self._marks(),
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "labels_saved": False, "label_note": LABEL_NOTE,
                "files": {"trial_results": "trial_results.jsonl",
                          "raw_serial_log": "raw_serial_log.txt" if self.input_kind == "serial" else None},
                "n_results": self.n_results, "n_raw_lines": self.n_raw, **self.info}
        try:
            check_no_labels(data)
            tmp = os.path.join(self.dir, "run_info.json.tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(clean(data), ensure_ascii=False, allow_nan=False, indent=1) + "\n")
            os.replace(tmp, os.path.join(self.dir, "run_info.json"))
            return True
        except (OSError, ValueError, TypeError) as e:
            return self._fail("run_info.json", e)

    # ---------- 시도 결과 ----------
    def write_result(self, result: dict) -> bool:
        if not self.ok:
            return False
        rec = {**result, **self._marks(), "source": result.get("source", self.source),
               "saved_at": dt.datetime.now().isoformat(timespec="milliseconds")}
        try:
            check_no_labels(rec)   # 라벨 키가 섞이면 저장하지 않는다 (ValueError)
            self._append("trial_results.jsonl", _dumps(rec) + "\n")
            self.n_results += 1
            return True
        except (OSError, ValueError, TypeError) as e:
            return self._fail("trial_results.jsonl", e)

    # ---------- 시리얼 원시 줄 ----------
    def write_raw(self, line: str) -> bool:
        """시리얼 입력원의 기기 줄 하나. 다른 입력원이면 쓰지 않는다 (재생 원본은 data/real에 이미 있음)."""
        if not self.ok or self.input_kind != "serial":
            return False
        try:
            if self._raw is None:
                self._raw = open(os.path.join(self.dir, "raw_serial_log.txt"), "a", encoding="utf-8", newline="\n")
            self._raw.write(f"{_rs.now_str()}\t{line}\n")
            self.n_raw += 1
            if self.n_raw % 50 == 0:   # 약 1초마다 디스크로
                self._raw.flush()
            return True
        except OSError as e:
            return self._fail("raw_serial_log.txt", e)

    def _close_raw(self):
        if self._raw is not None:
            try:
                self._raw.close()
            except OSError:
                pass
            self._raw = None

    def close(self, **info) -> None:
        if self._raw is not None:
            try:
                self._raw.flush()
            except OSError as e:
                self._fail("raw_serial_log.txt", e)
        self._close_raw()
        if self.ok:
            self.write_info(ended_at=dt.datetime.now().isoformat(timespec="seconds"), **info)
