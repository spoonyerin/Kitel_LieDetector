<#
.SYNOPSIS
  TruthPulse 발표용 서버 실행 (S18). 서버를 켜고, 끄면(Ctrl+C) 이 창의 환경 변수를 원래대로 되돌린다.

.DESCRIPTION
  환경 변수(SOURCE, SERIAL_PORT, QUESTION_SEED, SAVE)는 이 스크립트가 도는 동안만 설정하고,
  끝나면(Ctrl+C 포함) 실행 전 값으로 되돌린다. 그래서 창에 값이 남지 않는다.
  - .venv가 없으면 안내하고 끝낸다.
  - -Source serial인데 -Port가 없으면 포트 목록(포트를 열지 않음)을 보여 주고 끝낸다.
  - 실행 정책 때문에 실행되지 않으면 (정책 자체는 바꾸지 않는다):
      powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -Source replay
  - -WhatIf를 주면 설정만 보여 주고 서버는 켜지 않는다.

.PARAMETER Source
  serial(기기) | replay(저장된 로그 재생) | dummy(합성 신호). 기본 dummy.
.PARAMETER Port
  -Source serial일 때 기기 COM 포트 (예: COM6). 목록: .venv\Scripts\python.exe -m server.cli ports
.PARAMETER Seed
  질문 순서 시드 (QUESTION_SEED). 기본 7.
.PARAMETER Save
  서버 기록 저장 (SAVE=1, data\server\<날짜시각>_<입력원>\).

.EXAMPLE
  .\scripts\start_demo.ps1 -Source replay
.EXAMPLE
  .\scripts\start_demo.ps1 -Source serial -Port COM6 -Save
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('serial', 'replay', 'dummy')]
    [string]$Source = 'dummy',

    [ValidatePattern('^COM\d+$')]
    [string]$Port,

    [ValidateRange(0, 2147483647)]
    [int]$Seed = 7,

    [switch]$Save
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot          # scripts 폴더의 상위 = 저장소 루트
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Url = 'http://127.0.0.1:8000'

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "[문제] 가상환경이 없습니다: $Python"
    Write-Host '처음 한 번 저장소 루트에서 실행하세요 (docs/development.md 3절):'
    Write-Host '  py -3.13 -m venv .venv'
    Write-Host '  .venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit 1
}

if ($Source -eq 'serial' -and -not $Port) {
    Write-Host '[안내] 시리얼(기기)로 실행하려면 -Port가 필요합니다. 연결된 COM 포트 (포트를 열지 않음):'
    Push-Location -LiteralPath $Root
    try { & $Python -m server.cli ports } finally { Pop-Location }
    Write-Host ''
    Write-Host '예: .\scripts\start_demo.ps1 -Source serial -Port COM6'
    exit 2
}
if ($Port -and $Source -ne 'serial') {
    Write-Host "[안내] -Port $Port 는 -Source serial일 때만 씁니다. 무시합니다."
}

# 이 스크립트 동안만 쓸 환경 변수 (끝나면 원래 값으로 되돌린다)
$Vars = [ordered]@{
    SOURCE        = $Source
    SERIAL_PORT   = $(if ($Source -eq 'serial') { $Port } else { $null })
    QUESTION_SEED = [string]$Seed
    SAVE          = $(if ($Save) { '1' } else { '0' })
}
$Saved = @{}
foreach ($k in $Vars.Keys) { $Saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process') }

$Label = @{ serial = "기기 (시리얼 $Port)"; replay = '저장된 로그 재생'; dummy = '합성 신호 (더미)' }[$Source]
Write-Host '=============================================================='
Write-Host " TruthPulse 서버  입력원: $Label   질문 시드: $Seed   기록 저장: $(if ($Save) { '켬 (data\server\)' } else { '끔' })"
Write-Host " 브라우저 주소: $Url"
Write-Host ' 끄기: 이 창에서 Ctrl+C'
if ($Source -eq 'serial') {
    Write-Host ' 주의: 수집기·Arduino 시리얼 모니터를 닫고, 서버를 먼저 켠 뒤 기기에서 세션을 시작하세요.'
}
Write-Host '=============================================================='

$code = 0
Push-Location -LiteralPath $Root
try {
    foreach ($k in $Vars.Keys) { [Environment]::SetEnvironmentVariable($k, $Vars[$k], 'Process') }
    if ($PSCmdlet.ShouldProcess("$Url ($Label)", 'TruthPulse 서버 실행')) {
        & $Python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
        $code = $LASTEXITCODE
    }
}
finally {
    # Ctrl+C로 끝나도 실행된다: 환경 변수와 현재 폴더를 원래대로
    foreach ($k in $Saved.Keys) { [Environment]::SetEnvironmentVariable($k, $Saved[$k], 'Process') }
    Pop-Location
    Write-Host '서버를 끝냈습니다. 환경 변수는 실행 전 값으로 되돌렸습니다.'
}
exit $code
