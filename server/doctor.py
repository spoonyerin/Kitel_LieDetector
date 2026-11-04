"""발표 전 점검 (S18): python -m server.cli doctor

읽기 전용이다. 파일을 쓰지 않고, COM 포트를 열지 않는다 (목록만 본다).
필요한 패키지가 빠진 환경에서도 돌아가도록 이 파일은 표준 라이브러리와 server.config만 쓰고,
나머지는 점검하면서 하나씩 import해 본다.

항목: 파이썬 3.13, 필요한 패키지, web/dist, COM 포트 목록, 재생용 실측 로그, 질문 은행.
결과: [OK] / [주의](기능 일부만 제한) / [문제](해결해야 함) + 해결 방법 한 줄. 문제가 하나라도 있으면 종료 코드 1.
"""

import importlib
import os
import sys
from typing import Callable, List, NamedTuple, Optional

from server.config import DEFAULT_QUESTION_BANK, DEFAULT_REPLAY_RUN, REPO_ROOT, WEB_DIST

REQUIRED_PYTHON = (3, 13)
# (import 이름, pip 이름) — requirements.txt에서 서버·학습·테스트가 쓰는 것
PACKAGES = [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("websockets", "websockets"), ("numpy", "numpy"),
            ("scipy", "scipy"), ("serial", "pyserial"), ("pandas", "pandas"), ("sklearn", "scikit-learn"),
            ("pytest", "pytest")]
WEB_FILES = ("index.html", "app.mjs", "live.mjs", "core.mjs", "styles.css")
PIP_FIX = r".venv\Scripts\python.exe -m pip install -r requirements.txt"


class Check(NamedTuple):
    status: str      # OK | 주의 | 문제
    name: str
    detail: str
    fix: str = ""


def check_python(version_info=None) -> Check:
    v = version_info or sys.version_info
    text = f"{v[0]}.{v[1]}.{v[2]}"
    if tuple(v[:2]) == REQUIRED_PYTHON:
        return Check("OK", "파이썬", text)
    return Check("문제", "파이썬", f"{text} (필요: {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]})",
                 r"py -3.13 -m venv .venv 로 가상환경을 다시 만들고 패키지를 설치하세요 (docs/development.md 3절)")


def check_packages(importer: Callable = importlib.import_module) -> Check:
    missing, notes = [], []
    for mod, pip_name in PACKAGES:
        try:
            m = importer(mod)
        except Exception:
            missing.append(pip_name)
            continue
        if mod == "pandas":
            ver = str(getattr(m, "__version__", "0"))
            try:
                major = int(ver.split(".")[0])
            except ValueError:
                major = 0
            if major >= 3:
                notes.append(f"pandas {ver} (3 미만이어야 함)")
    if missing:
        return Check("문제", "패키지", "없음: " + ", ".join(missing), PIP_FIX)
    if notes:
        return Check("문제", "패키지", "; ".join(notes), PIP_FIX)
    return Check("OK", "패키지", f"{len(PACKAGES)}개 import 됨 (" + ", ".join(p for _, p in PACKAGES) + ")")


def check_web(web_dist: str = WEB_DIST) -> Check:
    lost = [f for f in WEB_FILES if not os.path.isfile(os.path.join(web_dist, f))]
    if lost:
        return Check("문제", "web/dist", "없는 파일: " + ", ".join(lost),
                     "저장소를 다시 받거나 git status로 web/dist가 지워졌는지 확인하세요")
    return Check("OK", "web/dist", f"화면 파일 {len(WEB_FILES)}개 있음")


def _list_ports():
    from serial.tools import list_ports as lp   # 목록만 본다. 포트를 열지 않는다.
    return [(p.device, p.description or "", p.hwid or "") for p in lp.comports()]


def check_ports(lister: Optional[Callable] = None) -> Check:
    try:
        ports = (lister or _list_ports)()
    except Exception as e:
        return Check("문제", "COM 포트", f"목록을 볼 수 없습니다: {e}", PIP_FIX + " (pyserial)")
    if not ports:
        return Check("주의", "COM 포트", "연결된 포트 없음 → 시리얼(기기) 불가, 재생·더미는 가능",
                     "기기 USB 케이블과 드라이버(CP210x/CH340 등)를 확인하세요")
    names = ", ".join(f"{d} ({desc})" if desc else d for d, desc, _ in ports)
    return Check("OK", "COM 포트", f"{len(ports)}개: {names}",
                 rf"기기 포트로 실행: .\scripts\start_demo.ps1 -Source serial -Port {ports[0][0]}")


def check_replay(run_dir: str = DEFAULT_REPLAY_RUN) -> Check:
    log = os.path.join(run_dir, "raw_serial_log.txt")
    rel = os.path.relpath(run_dir, REPO_ROOT) if run_dir.startswith(REPO_ROOT) else run_dir
    if os.path.isfile(log):
        return Check("OK", "재생 로그", f"{rel} 있음 → 재생 가능")
    return Check("주의", "재생 로그", f"{rel} 없음 → 재생 불가, 더미는 가능",
                 r"더미로 실행: .\scripts\start_demo.ps1 -Source dummy (재생하려면 data\real\experiments에 run 폴더를 복사)")


def check_question_bank(path: Optional[str] = None) -> Check:
    raw = path or os.environ.get("QUESTION_BANK") or DEFAULT_QUESTION_BANK
    bank = raw if os.path.isabs(raw) else os.path.join(REPO_ROOT, raw)
    fix = "collector/question_bank.csv가 있는지, QUESTION_BANK 환경 변수 경로가 맞는지 확인하세요"
    try:
        from server.questions import load_questions   # collector 코드를 읽기 전용으로 쓴다
        qs = load_questions(bank)
    except SystemExit:   # collector는 pyserial이 없으면 종료한다
        return Check("문제", "질문 은행", "pyserial이 없어 질문 은행 읽기 코드를 불러오지 못했습니다", PIP_FIX)
    except Exception as e:
        return Check("문제", "질문 은행", f"읽을 수 없음: {bank} ({e})", fix)
    if not qs:
        return Check("문제", "질문 은행", f"질문이 없습니다: {bank}", fix)
    return Check("OK", "질문 은행", f"{len(qs)}문항 ({os.path.relpath(bank, REPO_ROOT) if bank.startswith(REPO_ROOT) else bank})")


def run_checks(**kw) -> List[Check]:
    return [check_python(kw.get("version_info")), check_packages(kw.get("importer", importlib.import_module)),
            check_web(kw.get("web_dist", WEB_DIST)), check_ports(kw.get("lister")),
            check_replay(kw.get("replay_run", DEFAULT_REPLAY_RUN)), check_question_bank(kw.get("question_bank"))]


def print_checks(checks: List[Check], log=print) -> int:
    log("TruthPulse 점검 (읽기 전용: 파일을 쓰지 않고 COM 포트를 열지 않습니다)")
    for c in checks:
        log(f"[{c.status}] {c.name}: {c.detail}")
        if c.fix and c.status != "OK":
            log(f"        해결: {c.fix}")
        elif c.fix:
            log(f"        참고: {c.fix}")
    n_bad = sum(c.status == "문제" for c in checks)
    n_warn = sum(c.status == "주의" for c in checks)
    log(f"결과: 문제 {n_bad}개, 주의 {n_warn}개" + (" → 발표 준비 완료" if not n_bad else ""))
    return 1 if n_bad else 0


def main(argv=None) -> int:
    return print_checks(run_checks())


if __name__ == "__main__":
    sys.exit(main())
