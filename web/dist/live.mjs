// 기기 연동 모드: Python 서버의 /ws에 연결해 기기 단계, 그래프 샘플, 판정 결과를 받는다 (보기 전용).
// 이 파일은 DOM을 직접 다루지 않는다. 화면 갱신은 app.mjs가 하고, 여기에는 연결과 순수 함수만 둔다.

export const UNTRAINED_BADGE = '모델 미학습(데모)';
export const WINDOW_MS = 20000;            // 타임라인 그래프 구간 (기기 시각 기준)
export const MAX_RESULTS = 200;
export const MAX_EVENTS = 20;

// 입력원 이름. 재생과 더미는 서버가 무엇을 보내든 '실시간'으로 표시하지 않는다.
export const INPUT_LABELS = { replay: '저장된 로그 재생', dummy: '합성 신호(더미)', serial: '실시간 기기' };
export const isLive = status => !!status && status.input === 'serial' && status.live === true;
export function inputLabel(status) {
  if (!status) return '연결 대기';
  if (status.input === 'serial') {
    if (isLive(status)) return INPUT_LABELS.serial;
    return status.connected ? '기기 연결됨 · 신호 대기' : '기기 연결 끊김';
  }
  return INPUT_LABELS[status.input] ?? '알 수 없는 입력원';
}
export function signalHeading(status) {
  return isLive(status) ? '실시간 생체신호' : `생체신호 · ${inputLabel(status)}`;
}
export function streamStatusText(status, connection = 'open') {
  if (connection === 'disconnected') return '서버 연결 끊김 · 다시 연결하는 중';
  if (!status) return '서버 연결 중';
  if (status.finished) return status.input === 'replay' ? '재생이 끝났습니다' : '입력이 끝났습니다';
  if (status.input === 'replay') return '저장된 로그를 재생하는 중';
  if (status.input === 'dummy') return '합성 신호를 받는 중';
  if (isLive(status)) return '기기에서 측정하는 중';
  return status.connected ? '기기 신호를 기다리는 중' : '기기에 다시 연결하는 중';
}
export function noticeText(status) {
  const label = inputLabel(status);
  if (status?.input === 'replay') return [label, '예전에 기기로 기록한 신호를 다시 보여 주는 중입니다. 지금 측정하는 값이 아닙니다.'];
  if (status?.input === 'dummy') return [label, '하드웨어 없이 만든 합성 신호입니다. 실제 사람의 신호가 아닙니다.'];
  if (isLive(status)) return [label, '연결된 기기의 신호를 보여 줍니다.'];
  if (status?.input === 'serial') {
    return [label, status.connected ? '기기 포트는 열려 있고 신호를 기다리는 중입니다.' : '기기 포트에 다시 연결하는 중입니다.'];
  }
  return [label, '서버에 연결하는 중입니다.'];
}

// 기기 단계
export const PHASE_LABELS = {
  PLACE_HAND: '손 올리기', BASELINE: '기준 측정 중', READY: '질문 준비', QUESTION: '질문 중', RESPONSE: '답변 측정 중',
  ANALYZING: '분석 중', RESULT: '결과', ABORTED: '시도 취소', SESSION_END: '세션 종료',
};
export const PHASE_TEXT = {
  PLACE_HAND: ['센서에 손을 올려 주세요.', '손이 닿으면 기기 버튼으로 시작합니다.'],
  BASELINE: ['편안한 상태를 기록해요.', '기기가 약 15초 동안 개인 기준을 측정합니다. 움직이지 마세요.'],
  READY: ['질문을 준비해요.', '진행자가 기기 버튼을 누르고 질문을 읽습니다.'],
  QUESTION: ['질문을 듣는 중이에요.', '답하는 순간 진행자가 버튼을 누릅니다. 이 구간이 답변과 비교할 기준이 됩니다.'],
  RESPONSE: ['답변 속 변화를 읽고 있어요.', '약 20초 동안 답변 구간의 신호를 모읍니다.'],
  ANALYZING: ['변화를 분석하고 있어요.', '질문 구간과 답변 구간을 비교합니다.'],
  RESULT: ['결과를 확인하세요.', '짧게 누르면 다음 질문, 길게 누르면 세션을 마칩니다.'],
  ABORTED: ['이번 시도는 취소됐어요.', '신호 문제로 판정하지 않습니다. 기준을 다시 측정할 수 있습니다.'],
  SESSION_END: ['세션이 끝났어요.', '다음 참가자는 손을 올리고 버튼으로 시작합니다.'],
};
// 단계 표시. 재생과 더미에서는 참가자에게 하는 "손 올리기" 안내를 숨기고 입력원을 따라간다고 알린다.
export const FOLLOW_TEXT = {
  replay: ['측정은 저장된 로그를 따라갑니다.', '기록된 다음 세션이 시작되면 단계가 여기에 나타납니다.'],
  dummy: ['측정은 합성 신호를 따라갑니다.', '합성 신호의 다음 세션이 시작되면 단계가 여기에 나타납니다.'],
};
export function phaseDisplay(phase, status) {
  const follow = !isLive(status) ? FOLLOW_TEXT[status?.input] : null;
  if (follow && (!phase || phase === 'PLACE_HAND')) return { pill: null, title: follow[0], desc: follow[1] };
  const [title, desc] = PHASE_TEXT[phase] ?? ['기기 단계를 기다리는 중이에요.', '기기에서 세션을 시작하면 단계가 여기에 나타납니다.'];
  return { pill: phase ? (PHASE_LABELS[phase] ?? phase) : '기기 대기', title, desc };
}
// 세션 표시: 기기는 세션 밖 단계를 세션 0으로 보내므로, 서버가 붙여 준 세션 순번(S01, S02)을 쓴다.
export function sessionLabel(session) {
  if (!session?.index) return '세션 시작 전';
  return `세션 S${String(session.index).padStart(2, '0')}${session.active ? '' : ' · 종료'}`;
}
export function phaseStep(phase) {
  if (!phase || phase === 'PLACE_HAND' || phase === 'BASELINE') return 'baseline';
  if (phase === 'RESULT' || phase === 'ABORTED' || phase === 'SESSION_END') return 'result';
  return 'question';
}
export const EVENT_LABELS = {
  HAND_OK: '손 접촉 확인', HAND_LOST: '손이 센서에서 떨어졌습니다', CONTACT_DIP: '접촉이 약해졌습니다',
  CONTACT_SHIFT: '손 접촉 위치가 바뀌었습니다', CONTACT_BACK: '접촉이 돌아왔습니다', INVALID: '기기가 이번 시도를 무효로 처리했습니다',
  BASELINE_LOST: '기준 측정이 무효가 됐습니다 (재측정 필요)', SESSION_START: '세션이 시작됐습니다', SESSION_END: '세션이 끝났습니다',
  DEVICE_REBOOT: '기기가 다시 시작됐습니다', PPG_STALL: 'PPG 신호가 멈췄습니다', PPG_RECOVERED: 'PPG 신호가 돌아왔습니다',
};
export const TOAST_EVENTS = new Set(['HAND_LOST', 'CONTACT_SHIFT', 'CONTACT_DIP', 'INVALID', 'BASELINE_LOST', 'DEVICE_REBOOT']);
export const eventLabel = event => EVENT_LABELS[event?.name] ?? event?.name ?? '';

// 판정 결과 → 화면 모델
export const DECISION_LABELS = { truth: '진실', lie: '거짓', inconclusive: '보류' };
export const SENSOR_LABELS = { gsr: '피부전도 GSR', fsr: '손가락 압력 FSR', ppg: '맥파 PPG' };
export const resultKey = r => [r.source, r.session_id, r.trial_id, r.baseline_id].join('|');
// 화면에 보이지 않는 경고 (서버 기록에만 남긴다). 서버가 빼고 보내지만 한 번 더 거른다.
export const HIDDEN_WARNING_PREFIXES = ['UNKNOWN_WARN_BITS'];
export const visibleWarnings = ws => (ws ?? []).filter(w => !HIDDEN_WARNING_PREFIXES.some(p => String(w).startsWith(p)));
export function trialView(r) {
  const decision = DECISION_LABELS[r.decision] ? r.decision : 'inconclusive';
  return {
    key: resultKey(r),
    title: `${r.session_id} · 질문 ${r.trial_id}`,
    decision,
    decisionLabel: DECISION_LABELS[decision],
    kind: decision === 'lie' ? 'warn' : decision === 'inconclusive' ? 'poor' : '',
    reasons: Array.isArray(r.reasons) ? r.reasons : [],
    holdReason: decision === 'inconclusive' ? (r.hold_reason ?? '판정을 보류했습니다.') : null,
    badge: r.judge?.trained === true ? null : UNTRAINED_BADGE,
    judgeLabel: `${r.judge?.name ?? '?'} ${r.judge?.version ?? ''}`.trim(),
    judgeNote: r.judge_note ?? '',
    disclaimer: r.disclaimer ?? '',
    sourceLabel: r.source === 'dummy' ? '합성 신호(더미)' : '기기 기록',
    warnings: visibleWarnings(r.quality?.warnings),
    // 기여도 1 = 임계값. 막대는 임계값을 50%로, 2배 이상은 100%로 자른다 (화면 표시용).
    contributions: (r.contributions ?? []).map(c => ({
      sensor: c.sensor, label: SENSOR_LABELS[c.sensor] ?? c.sensor,
      text: `${c.value >= 0 ? '+' : '−'}${Math.abs(c.value).toFixed(1)}σ`,
      width: Math.max(2, Math.min(100, Math.abs(c.contribution) * 50)),
      over: !!c.over_threshold, direction: c.direction,
    })),
  };
}

// 심박 추정값 (화면 표시용, 판정에는 쓰지 않는다). 값이 없으면 "—"와 그 이유.
export function bpmView(msg) {
  if (msg && Number.isFinite(msg.bpm)) {
    return { value: String(Math.round(msg.bpm)), unit: 'BPM 추정값', foot: `추정값 · 최근 ${msg.window_sec ?? 10}초` };
  }
  const why = msg?.reason && msg.reason !== 'NOT_ENOUGH_DATA' && msg.reason_text ? ` · ${msg.reason_text}` : '';
  return { value: '—', unit: 'BPM', foot: `심박 계산 전${why}` };
}

// 상태
export const initialState = () => ({ status: null, phase: null, session: null, bpm: null, events: [], baseline: null, results: [], samples: [], error: null });
export function batchToPoints(b) {
  const n = Array.isArray(b?.time_ms) ? b.time_ms.length : 0;
  return Array.from({ length: n }, (_, i) => ({
    t: b.time_ms[i], fsr: b.fsr[i], gsr: b.gsr[i], ir: b.ir[i], red: b.red?.[i], phase: b.phase?.[i], contact: b.contact?.[i],
  }));
}
export function reduce(state, msg) {
  switch (msg?.type) {
    // 시리얼이 다시 연결되면 지난 연결 오류(띠)를 지운다
    case 'device_status': return { ...state, status: msg, error: msg.input === 'serial' && msg.connected ? null : state.error };
    case 'phase_update': {
      let next = state;
      if (msg.session_index) next = { ...next, session: { index: msg.session_index, active: msg.session_active === true } };
      if (msg.phase) next = { ...next, phase: msg };
      if (msg.event) next = { ...next, events: [...next.events, msg.event].slice(-MAX_EVENTS) };
      return next;
    }
    case 'baseline_result': return { ...state, baseline: msg };
    case 'bpm_update': return { ...state, bpm: msg };
    case 'trial_result': {
      const key = resultKey(msg);
      const results = [...state.results.filter(r => resultKey(r) !== key), msg].slice(-MAX_RESULTS);
      return { ...state, results };
    }
    case 'sample_batch': {
      const pts = batchToPoints(msg);
      if (!pts.length) return state;
      let samples = state.samples;
      // 기기 시각이 크게 거꾸로 가면(재부팅, 재생 처음) 그래프를 비운다
      if (samples.length && pts[0].t < samples.at(-1).t - 1000) samples = [];
      samples = [...samples, ...pts];
      const end = samples.at(-1).t;
      return { ...state, samples: samples.filter(p => end - p.t <= WINDOW_MS) };
    }
    case 'error': return { ...state, error: msg };
    default: return state;
  }
}
export const latestResult = state => state.results.at(-1) ?? null;

// 그래프용: 0~1로 정규화 (화면 표시용 상대 크기)
export function normalize(values) {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return values.map(() => 0.5);
  const lo = Math.min(...finite), hi = Math.max(...finite);
  if (hi - lo < 1e-9) return values.map(() => 0.5);
  return values.map(v => Number.isFinite(v) ? (v - lo) / (hi - lo) : 0.5);
}

// 연결
export function wsUrl(loc) {
  return `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/ws`;
}
// 처음 연결에 실패하면 'unavailable'을 알리고 멈춘다 (예전 데모 서버처럼 /ws가 없는 경우).
// 한 번이라도 연결된 뒤 끊기면 'disconnected'를 알리고 1초부터 최대 5초 간격으로 다시 연결한다.
export function connectLive({ url, onMessage, onState, WebSocketImpl = globalThis.WebSocket,
  setTimer = setTimeout, clearTimer = clearTimeout, minDelay = 1000, maxDelay = 5000 }) {
  let ws = null, timer = null, everOpened = false, closed = false, delay = minDelay;
  function open() {
    if (closed) return;
    onState?.(everOpened ? 'reconnecting' : 'connecting');
    try { ws = new WebSocketImpl(url); } catch { return failed(); }
    ws.onopen = () => { const again = everOpened; everOpened = true; delay = minDelay; onState?.('open', { reconnected: again }); };
    ws.onmessage = ev => { let msg; try { msg = JSON.parse(ev.data); } catch { return; } onMessage?.(msg); };
    ws.onclose = () => failed();
    ws.onerror = () => {};
  }
  function failed() {
    if (closed) return;
    ws = null;
    if (!everOpened) { onState?.('unavailable'); return; }
    onState?.('disconnected', { retryIn: delay });
    timer = setTimer(open, delay);
    delay = Math.min(maxDelay, delay * 2);
  }
  open();
  return { close() { closed = true; if (timer) clearTimer(timer); try { ws?.close(); } catch { /* 무시 */ } } };
}
