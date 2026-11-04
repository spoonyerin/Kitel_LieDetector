"""발표용 실행 스크립트(S18) 테스트: scripts/start_demo.ps1 (문법·인자 검사 수준).

스크립트는 실행하지 않는다 (서버를 켜게 되므로). 파이썬으로 내용을 정적으로 검사하고,
PowerShell 구문 분석기 검사는 환경 변수 TRUTHPULSE_PS_PARSE=1일 때만 돈다 (스크립트를 실행하지 않고 분석만 함).
"""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


SCRIPT = os.path.join(ROOT, "scripts", "start_demo.ps1")


def _script_text():
    with open(SCRIPT, "rb") as f:
        raw = f.read()
    return raw, raw.decode("utf-8-sig")


# ---------- start_demo.ps1 ----------
def test_script_encoding_for_windows_powershell():
    raw, text = _script_text()
    assert raw.startswith(b"\xef\xbb\xbf")          # BOM: Windows PowerShell 5.1이 한글을 UTF-8로 읽게
    assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n")


def test_script_parameters():
    _, text = _script_text()
    m = re.search(r"\n\[CmdletBinding\(SupportsShouldProcess = \$true\)\]\s*\nparam\((.*?)\n\)\s*\n", text, re.S)
    assert m, "param 블록"
    block = m.group(1)
    assert re.search(r"\[ValidateSet\('serial', 'replay', 'dummy'\)\]\s*\[string\]\$Source = 'dummy'", block)
    assert re.search(r"\[ValidatePattern\('\^COM\\d\+\$'\)\]\s*\[string\]\$Port\b", block)
    assert re.search(r"\[int\]\$Seed = 7\b", block)
    assert re.search(r"\[switch\]\$Save\b", block)
    names = re.findall(r"\]\s*\$(\w+)", block)
    assert names == ["Source", "Port", "Seed", "Save"]


def test_script_env_only_inside_process_and_restored():
    _, text = _script_text()
    # 환경 변수는 프로세스 범위로만 쓰고, finally에서 원래 값으로 되돌린다
    assert "SetEnvironmentVariable($k, $Vars[$k], 'Process')" in text
    assert re.search(r"finally \{\s*\n\s*#[^\n]*\n\s*foreach \(\$k in \$Saved\.Keys\) \{ \[Environment\]::SetEnvironmentVariable\(\$k, \$Saved\[\$k\], 'Process'\) \}", text)
    for k in ("SOURCE", "SERIAL_PORT", "QUESTION_SEED", "SAVE"):
        assert re.search(rf"^\s+{k}\s+= ", text, re.M), k
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r"<#.*?#>", "", code, flags=re.S)
    assert "$env:" not in code                       # $env: 대입(창에 남음) 대신 Process 범위 API만 쓴다
    for bad in ("Set-ExecutionPolicy", "setx", "'User'", "'Machine'", "Remove-Item Env:"):
        assert bad not in code, bad
    assert ".Write(" not in code and "SerialPort" not in code   # 기기로 쓰지 않는다


def test_script_behaviour_text():
    _, text = _script_text()
    assert "Test-Path -LiteralPath $Python" in text and "py -3.13 -m venv .venv" in text and "exit 1" in text
    assert re.search(r"if \(\$Source -eq 'serial' -and -not \$Port\) \{.*?server\.cli ports.*?exit 2", text, re.S)
    assert "-m uvicorn server.app:app --host 127.0.0.1 --port 8000" in text
    assert "http://127.0.0.1:8000" in text and "Ctrl+C" in text
    assert "powershell -ExecutionPolicy Bypass -File scripts\\start_demo.ps1" in text   # 안내 문구 (정책은 바꾸지 않음)


def test_script_brackets_balanced():
    """간단한 구문 점검: 문자열·주석 밖의 괄호 짝 (PowerShell 분석기 검사는 아래 선택 테스트)."""
    _, text = _script_text()
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    stack, pairs = [], {")": "(", "]": "[", "}": "{"}
    for line in text.splitlines():
        i, quote = 0, None
        while i < len(line):
            c = line[i]
            if quote:
                if c == quote:
                    quote = None
            elif c in "'\"":
                quote = c
            elif c == "#":
                break
            elif c in "([{":
                stack.append(c)
            elif c in ")]}":
                assert stack and stack.pop() == pairs[c], line
            i += 1
        assert quote is None, f"따옴표가 닫히지 않음: {line}"
    assert not stack


@pytest.mark.skipif(os.environ.get("TRUTHPULSE_PS_PARSE") != "1" or not shutil.which("powershell"),
                    reason="선택 검사: TRUTHPULSE_PS_PARSE=1일 때만 PowerShell 구문 분석기로 확인 (스크립트는 실행하지 않음)")
def test_script_powershell_parser():
    cmd = ("$t=$null; $e=$null; $a=[System.Management.Automation.Language.Parser]::ParseFile("
           f"'{SCRIPT}',[ref]$t,[ref]$e); "
           "@{ errors=@($e | ForEach-Object { $_.Message }); "
           "params=@($a.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath }) } | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                         capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    res = json.loads(out.strip().splitlines()[-1])
    assert res["errors"] in ([], None) and res["params"] == ["Source", "Port", "Seed", "Save"]
