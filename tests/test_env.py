"""개발 환경 확인 테스트: 필요한 패키지가 설치되어 임포트되는지 본다."""
import importlib
import sys

import pytest

# requirements.txt의 핵심 패키지 (임포트 이름 기준)
PACKAGES = ["serial", "numpy", "scipy", "pandas", "sklearn", "fastapi", "uvicorn"]


def test_python_version():
    """3.13 이상에서 실행되는지 확인한다."""
    assert sys.version_info >= (3, 13)


@pytest.mark.parametrize("name", PACKAGES)
def test_package_importable(name):
    """패키지를 임포트할 수 있는지 확인한다."""
    importlib.import_module(name)


def test_pandas_below_3():
    """pandas 3의 동작 변경을 피하려고 2.x로 고정했는지 확인한다."""
    major = int(importlib.import_module("pandas").__version__.split(".")[0])
    assert major < 3
