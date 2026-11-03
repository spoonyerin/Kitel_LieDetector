"""줄 해석기(server/source/lines.py) 테스트.

- 합성 줄로 샘플, 이벤트, 요약(헤더 있음/폴백/불일치), nan, 잘못된 줄을 확인한다.
- collector 상수를 복사하지 않고 같은 객체로 재사용하는지 확인한다.
- 실제 로그(data/real, 읽기 전용)의 요약과 이벤트 해석 결과가
  collector/recover_data.py의 parse_raw_log와 같은지 비교한다. 데이터가 없으면 건너뛴다.
"""

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server.source import lines  # noqa: E402
from server.source.lines import (Bad, BaselineSummary, Boot, Event, Header,  # noqa: E402
                                 LineParser, Message, Sample, TrialSummary, iter_raw_log)

import record_session  # noqa: E402  (lines가 collector 경로를 추가한다)
import recover_data  # noqa: E402

SUMMARY_VALID = ("#SUMMARY,1,2,2,1,NONE,0,741,2205.74,62.98,2251.78,12.08,101961.6,"
                 "216,4324,999,2007.84,161.97,2286.73,8.84,104661.9,-3.142,2.892")
SUMMARY_NAN = ("#SUMMARY,1,1,1,0,HAND_OFF_LONG,0,750,641.97,128.95,2233.39,22.64,105593.9,"
               "0,0,0,0.00,0.00,0.00,0.00,0.0,nan,nan")
BASELINE_LINE = "#BASELINE,1,1,1,NONE,0,749,1873.25,43.46,1406.65,30.86,112908.7"


# ---------- collector 재사용 ----------
def test_reuses_collector_objects():
    """상수와 함수가 collector의 같은 객체여야 한다 (복사본이면 구조가 어긋날 수 있다)."""
    assert lines.SUMMARY_HEADER_V5 is record_session.SUMMARY_HEADER_V5
    assert lines.BASELINE_HEADER_V5 is record_session.BASELINE_HEADER_V5
    assert lines.FW_SAMPLE_HEADER is record_session.FW_SAMPLE_HEADER
    assert lines.WARN_BITS is record_session.WARN_BITS
    assert lines.check_segment is record_session.check_segment
    assert lines.warn_names is record_session.warn_names


# ---------- 샘플 ----------
def test_sample_line():
    item = LineParser().feed("62441,773,2233,105696,90223,BASELINE,0,1,1,3095,1")
    assert isinstance(item, Sample)
    assert (item.time_ms, item.fsr, item.gsr, item.ir, item.red) == (62441, 773, 2233, 105696, 90223)
    assert (item.phase, item.trial, item.session, item.baseline_id, item.seq, item.contact) == \
        ("BASELINE", 0, 1, 1, 3095, 1)
    # check_segment가 받는 형식 (Run.on_sample의 smp 튜플과 같은 순서)
    assert item.as_collector_tuple() == (62441, 773, 2233, 105696, 90223, "BASELINE", 3095, 1)


def test_sample_wrong_field_count_is_bad():
    item = LineParser().feed("62441,773,2233,105696,90223,BASELINE,0,1,1,3095")
    assert isinstance(item, Bad) and item.kind == "sample"


def test_sample_non_integer_is_bad():
    item = LineParser().feed("62441,773,abc,105696,90223,BASELINE,0,1,1,3095,1")
    assert isinstance(item, Bad) and item.kind == "sample"


def test_sample_header():
    p = LineParser()
    item = p.feed(lines.FW_SAMPLE_HEADER)
    assert isinstance(item, Header) and item.kind == "sample" and item.ok
    assert p.header_ok is True
    item = p.feed("time_ms,fsr,gsr")
    assert isinstance(item, Header) and not item.ok
    assert p.header_ok is False


# ---------- 이벤트 ----------
def test_event_line():
    item = LineParser().feed("#EVENT,130174,RESPONSE,2,START,1,2")
    assert isinstance(item, Event)
    assert (item.esp_time_ms, item.name, item.trial, item.detail, item.session, item.baseline_id) == \
        (130174, "RESPONSE", 2, "START", 1, 2)


def test_button_event_detail_kept():
    item = LineParser().feed("#EVENT,727369,BUTTON,0,PLACE_HAND:PRESS:START_SESSION:IRQ,0,0")
    assert isinstance(item, Event) and item.detail == "PLACE_HAND:PRESS:START_SESSION:IRQ"


def test_event_short_or_bad():
    assert isinstance(LineParser().feed("#EVENT,1,RESULT,1,START,1"), Bad)
    assert isinstance(LineParser().feed("#EVENT,x,RESULT,1,START,1,1"), Bad)


# ---------- 요약 ----------
def test_summary_fallback_without_header():
    p = LineParser()
    item = p.feed(SUMMARY_VALID)
    assert isinstance(item, TrialSummary)
    assert item.header_source == "fallback_v5"
    assert (item.session, item.baseline_id, item.trial) == (1, 2, 2)
    assert item.valid is True and item.reason == "NONE" and item.warn_flags == 0
    assert item.values["fsr_dz"] == pytest.approx(-3.142)
    assert item.values["gsr_dz"] == pytest.approx(2.892)
    assert item.raw["gsr_dz"] == "2.892"  # 원래 문자열도 보관
    # Run과 같이 폴백 헤더를 이후에도 쓴다
    assert p.summary_header is lines.SUMMARY_HEADER_V5


def test_summary_with_received_header():
    p = LineParser()
    hdr = "#SUMMARY_HEADER," + ",".join(lines.SUMMARY_HEADER_V5)
    assert isinstance(p.feed(hdr), Header)
    item = p.feed(SUMMARY_VALID)
    assert isinstance(item, TrialSummary) and item.header_source == "received"


def test_summary_nan_and_invalid():
    item = LineParser().feed(SUMMARY_NAN)
    assert isinstance(item, TrialSummary)
    assert item.valid is False and item.reason == "HAND_OFF_LONG"
    assert math.isnan(item.values["fsr_dz"]) and math.isnan(item.values["gsr_dz"])


def test_summary_field_mismatch_is_bad():
    # 헤더 없이 필드 수가 틀리면 폴백하지 않는다
    assert isinstance(LineParser().feed("#SUMMARY,1,2,3"), Bad)
    # 받은 헤더와 필드 수가 다르면 버린다
    p = LineParser()
    p.feed("#SUMMARY_HEADER,session,baseline_id,trial")
    item = p.feed(SUMMARY_VALID)
    assert isinstance(item, Bad) and item.kind == "summary"


def test_summary_warn_names():
    line = SUMMARY_VALID.replace(",NONE,0,", ",NONE,20,")  # 4(FSR_SAT) + 16(CONTACT_DIP)
    item = LineParser().feed(line)
    assert item.warn_flags == 20
    assert item.warn_names == record_session.warn_names("20")


def test_baseline_summary():
    p = LineParser()
    item = p.feed(BASELINE_LINE)
    assert isinstance(item, BaselineSummary)
    assert item.header_source == "fallback_v5" and item.valid and item.baseline_id == 1
    assert item.values["gsr_mean"] == pytest.approx(1406.65)


def test_baseline_header_line_is_not_baseline_summary():
    item = LineParser().feed("#BASELINE_HEADER," + ",".join(lines.BASELINE_HEADER_V5))
    assert isinstance(item, Header) and item.kind == "baseline"


# ---------- 기타 ----------
def test_boot_message_empty():
    p = LineParser()
    assert isinstance(p.feed("#BOOT,KITEL_MAIN_V5"), Boot)
    assert isinstance(p.feed("#INFO something"), Message)
    assert p.feed("") is None


def test_iter_raw_log(tmp_path):
    """탭 앞은 PC 시각, 탭 뒤는 기기 줄. 빈 기기 줄은 건너뛴다. (tmp_path에만 쓴다)"""
    f = tmp_path / "raw_serial_log.txt"
    f.write_text("2026-11-03 15:00:00.000\t#BOOT,KITEL_MAIN_V5\n"
                 "2026-11-03 15:00:00.001\t\n"
                 "2026-11-03 15:00:00.002\t62441,773,2233,105696,90223,BASELINE,0,1,1,3095,1 \r\n",
                 encoding="utf-8")
    got = list(iter_raw_log(str(f)))
    assert got == [("2026-11-03 15:00:00.000", "#BOOT,KITEL_MAIN_V5"),
                   ("2026-11-03 15:00:00.002", "62441,773,2233,105696,90223,BASELINE,0,1,1,3095,1")]


# ---------- 실제 로그와 collector 해석 결과 비교 (읽기 전용) ----------
REAL_DIR = os.path.join(ROOT, "data", "real", "experiments")
REAL_RUNS = ["run_20261103_152007", "run_20261103_152345",
             "run_20261103_154858", "run_20261103_161545"]


def _parse_with_server(log_path):
    """recover_data.parse_raw_log와 같은 모양의 사전으로 모은다 (같은 키는 나중 값)."""
    p = LineParser()
    summary_map, event_map, baseline_map = {}, {}, {}
    for _pc, line in iter_raw_log(log_path):
        item = p.feed(line)
        if isinstance(item, TrialSummary):
            summary_map[(item.session, item.trial)] = item.raw
        elif isinstance(item, BaselineSummary):
            baseline_map[(item.session, item.baseline_id)] = item.raw
        elif isinstance(item, Event):
            event_map.setdefault((item.session, item.trial), []).append((item.name, item.detail))
    return summary_map, event_map, baseline_map


@pytest.mark.parametrize("run", REAL_RUNS)
def test_matches_collector_parse_raw_log(run):
    log_path = os.path.join(REAL_DIR, run, "raw_serial_log.txt")
    if not os.path.isfile(log_path):
        pytest.skip(f"실측 로그 없음: {run}")
    expected = recover_data.parse_raw_log(log_path)
    got = _parse_with_server(log_path)
    assert got[0] == expected[0], "SUMMARY 해석 결과가 collector와 다르다"
    assert got[1] == expected[1], "EVENT 해석 결과가 collector와 다르다"
    assert got[2] == expected[2], "BASELINE 해석 결과가 collector와 다르다"


def test_real_log_161545_expected_counts():
    """M1 재생 시험에서 확인한 값: SUMMARY 11개 (valid=1 10개, HAND_OFF_LONG 1개), BASELINE 3개."""
    log_path = os.path.join(REAL_DIR, "run_20261103_161545", "raw_serial_log.txt")
    if not os.path.isfile(log_path):
        pytest.skip("실측 로그 없음")
    p = LineParser()
    items = [p.feed(line) for _pc, line in iter_raw_log(log_path)]
    sums = [x for x in items if isinstance(x, TrialSummary)]
    bases = [x for x in items if isinstance(x, BaselineSummary)]
    assert len(sums) == 11
    assert sum(1 for s in sums if s.valid) == 10
    assert [s.reason for s in sums if not s.valid] == ["HAND_OFF_LONG"]
    assert len(bases) == 3
