"""발표 전 점검(python -m server.cli doctor, server/doctor.py, S18) 테스트.

가짜 포트 목록·가짜 경로·가짜 import를 넣어 항목별 OK/주의/문제와 해결 문장을 확인한다. 포트를 열지 않고 파일을 쓰지 않는다.
"""

import io
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import doctor as D  # noqa: E402


def test_doctor_python_version():
    assert D.check_python((3, 13, 5)).status == "OK"
    bad = D.check_python((3, 12, 1))
    assert bad.status == "문제" and "3.12.1" in bad.detail and "venv" in bad.fix


def test_doctor_packages(monkeypatch):
    import importlib
    real = importlib.import_module
    assert D.check_packages(real).status == "OK"

    def no_scipy(name):
        if name in ("scipy", "serial"):
            raise ImportError(name)
        return real(name)
    c = D.check_packages(no_scipy)
    assert c.status == "문제" and "scipy" in c.detail and "pyserial" in c.detail and "requirements.txt" in c.fix

    class P3:
        __version__ = "3.0.1"
    c = D.check_packages(lambda n: P3 if n == "pandas" else real(n))
    assert c.status == "문제" and "3 미만" in c.detail


def test_doctor_web_dist(tmp_path):
    assert D.check_web().status == "OK"
    (tmp_path / "index.html").write_text("x", encoding="utf-8")
    c = D.check_web(str(tmp_path))
    assert c.status == "문제" and "app.mjs" in c.detail


def test_doctor_ports_never_opens():
    c = D.check_ports(lambda: [("COM6", "Silicon Labs CP210x", "USB VID:PID=10C4:EA60")])
    assert c.status == "OK" and "COM6" in c.detail and "-Port COM6" in c.fix
    c = D.check_ports(lambda: [])
    assert c.status == "주의" and "재생·더미는 가능" in c.detail

    def broken():
        raise OSError("no access")
    assert D.check_ports(broken).status == "문제"
    # 기본 목록 함수는 serial.tools.list_ports만 쓴다 (포트 열기 없음)
    import inspect
    src = inspect.getsource(D._list_ports)
    assert "comports" in src and "Serial(" not in src


def test_doctor_replay(tmp_path):
    c = D.check_replay(str(tmp_path / "run_없음"))
    assert c.status == "주의" and "재생 불가" in c.detail and "더미는 가능" in c.detail and "-Source dummy" in c.fix
    run = tmp_path / "run_x"
    run.mkdir()
    (run / "raw_serial_log.txt").write_text("", encoding="utf-8")
    assert D.check_replay(str(run)).status == "OK"


def test_doctor_question_bank(tmp_path, monkeypatch):
    monkeypatch.delenv("QUESTION_BANK", raising=False)
    c = D.check_question_bank()
    assert c.status == "OK" and "15문항" in c.detail
    c = D.check_question_bank(str(tmp_path / "없음.csv"))
    assert c.status == "문제" and "QUESTION_BANK" in c.fix
    monkeypatch.setenv("QUESTION_BANK", str(tmp_path / "없음2.csv"))
    assert D.check_question_bank().status == "문제"


def test_doctor_report_and_exit_code(tmp_path):
    ok = D.run_checks(lister=lambda: [], replay_run=str(tmp_path / "없음"))
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = D.print_checks(ok)
    text = buf.getvalue()
    assert code == 0 and "문제 0개, 주의 2개" in text and "해결:" in text
    assert [c.name for c in ok] == ["파이썬", "패키지", "web/dist", "COM 포트", "재생 로그", "질문 은행"]
    bad = D.run_checks(lister=lambda: [], web_dist=str(tmp_path))
    with redirect_stdout(io.StringIO()):
        assert D.print_checks(bad) == 1


def test_doctor_cli_is_read_only(tmp_path, monkeypatch):
    from server.cli import main
    monkeypatch.setattr(D, "_list_ports", lambda: [])
    before = set(os.listdir(ROOT))
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["doctor"])
    assert code in (0, 1) and "TruthPulse 점검" in buf.getvalue()
    assert set(os.listdir(ROOT)) == before
