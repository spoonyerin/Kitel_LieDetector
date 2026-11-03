import test from 'node:test';
import assert from 'node:assert/strict';
import {
  UNTRAINED_BADGE, WINDOW_MS, batchToPoints, connectLive, initialState, inputLabel, isLive, latestResult,
  normalize, noticeText, phaseDisplay, phaseStep, reduce, sessionLabel, signalHeading, streamStatusText, trialView,
  visibleWarnings, wsUrl,
} from '../dist/live.mjs';

const status = (input, extra = {}) => ({ type: 'device_status', input, live: false, connected: true, finished: false, ...extra });
const result = (extra = {}) => ({
  type: 'trial_result', source: 'hardware', session_id: 'S02', trial_id: 1, baseline_id: 1, decision: 'lie', score: null,
  judge: { name: 'rule', version: '0.0.1', trained: false },
  judge_note: '규칙 판정 임계값(FSR 3.0σ, GSR 3.0σ, 1개 센서 이상)은 통계에서 흔히 쓰는 관례로 정한 값이며, 라벨 데이터로 맞추거나 검증하지 않았습니다.',
  disclaimer: '오락용 게임입니다. 이유는 통계적 설명이며 거짓의 증명이 아닙니다.',
  quality: { ok: true, flags: [], warnings: [] },
  contributions: [
    { sensor: 'fsr', feature: 'fsr_local_dz', value: -3.71, contribution: 1.237, direction: 'down', over_threshold: true },
    { sensor: 'gsr', feature: 'gsr_local_dz', value: -2.77, contribution: 0.923, direction: 'down', over_threshold: false },
  ],
  reasons: ['답변 중 손가락 압력(FSR)이 질문 구간보다 3.7σ 낮았습니다 (임계값 3.0σ).'], hold_reason: null, ...extra,
});

test('replay and dummy are never labelled as real-time measurement', () => {
  for (const input of ['replay', 'dummy']) {
    for (const live of [false, true]) { // 서버가 live=true를 잘못 보내도
      const s = status(input, { live });
      assert.equal(isLive(s), false);
      for (const text of [inputLabel(s), signalHeading(s), streamStatusText(s), ...noticeText(s)]) {
        assert.ok(!text.includes('실시간'), `${input}: "${text}"`);
      }
    }
  }
  assert.equal(inputLabel(status('replay')), '저장된 로그 재생');
  assert.equal(inputLabel(status('dummy')), '합성 신호(더미)');
  assert.equal(inputLabel(status('serial', { live: true })), '실시간 기기');
  assert.equal(signalHeading(status('serial', { live: true })), '실시간 생체신호');
  assert.equal(streamStatusText(status('replay', { finished: true })), '재생이 끝났습니다');
  assert.match(streamStatusText(status('dummy'), 'disconnected'), /연결 끊김/);
});

test('serial is real-time only while the device is sending lines', () => {
  const sending = status('serial', { live: true, connected: true });
  const waiting = status('serial', { live: false, connected: true });
  const lost = status('serial', { live: false, connected: false });
  assert.equal(inputLabel(sending), '실시간 기기'); assert.equal(streamStatusText(sending), '기기에서 측정하는 중');
  assert.equal(inputLabel(waiting), '기기 연결됨 · 신호 대기'); assert.equal(streamStatusText(waiting), '기기 신호를 기다리는 중');
  assert.equal(inputLabel(lost), '기기 연결 끊김'); assert.equal(streamStatusText(lost), '기기에 다시 연결하는 중');
  for (const s of [waiting, lost]) {
    for (const text of [inputLabel(s), signalHeading(s), streamStatusText(s), ...noticeText(s)]) assert.ok(!text.includes('실시간'), text);
  }
  assert.equal(signalHeading(sending), '실시간 생체신호');
});

test('serial reconnect clears the previous connection error', () => {
  let s = initialState();
  s = reduce(s, { type: 'error', retryable: true, message: '기기 연결이 끊겼습니다 (COM6)' });
  s = reduce(s, status('serial', { connected: false }));
  assert.ok(s.error);
  s = reduce(s, status('serial', { connected: true }));
  assert.equal(s.error, null);
  // 재생/더미의 오류는 상태 메시지로 지우지 않는다
  let r = reduce(initialState(), { type: 'error', message: '원시 로그가 없습니다' });
  r = reduce(r, status('replay', { connected: true }));
  assert.ok(r.error);
});

test('trial view shows decision, reasons, untrained badge, judge note and disclaimer', () => {
  const v = trialView(result());
  assert.equal(v.decisionLabel, '거짓'); assert.equal(v.kind, 'warn');
  assert.equal(v.badge, UNTRAINED_BADGE); assert.equal(v.badge, '모델 미학습(데모)');
  assert.match(v.judgeNote, /관례로 정한 값이며, 라벨 데이터로 맞추거나 검증하지 않았습니다/);
  assert.match(v.disclaimer, /오락용.*증명이 아닙니다/);
  assert.deepEqual(v.reasons, ['답변 중 손가락 압력(FSR)이 질문 구간보다 3.7σ 낮았습니다 (임계값 3.0σ).']);
  assert.equal(v.holdReason, null);
  const [fsr, gsr] = v.contributions;
  assert.equal(fsr.text, '−3.7σ'); assert.equal(fsr.over, true); assert.ok(fsr.width > 50); // 임계값 = 50%
  assert.equal(gsr.over, false); assert.ok(gsr.width < 50);
  assert.equal(trialView(result({ decision: 'truth' })).decisionLabel, '진실');
});

test('held trial shows the hold reason and the badge stays', () => {
  const v = trialView(result({ decision: 'inconclusive', contributions: [], reasons: ['손 위치가 바뀌어 이 답변은 측정에서 제외했어요.'],
    hold_reason: '손 위치가 바뀌어 이 답변은 측정에서 제외했어요.' }));
  assert.equal(v.decisionLabel, '보류'); assert.equal(v.kind, 'poor');
  assert.equal(v.holdReason, '손 위치가 바뀌어 이 답변은 측정에서 제외했어요.'); assert.equal(v.badge, UNTRAINED_BADGE);
  assert.equal(trialView(result({ decision: 'weird' })).decisionLabel, '보류'); // 모르는 값은 보류로
  assert.equal(trialView(result({ judge: { name: 'logreg', version: '1', trained: true } })).badge, null);
});

test('state keeps status, phase, events, baseline and de-duplicates resent results', () => {
  let s = initialState();
  s = reduce(s, status('replay'));
  s = reduce(s, { type: 'phase_update', phase: 'RESPONSE', trial: 2, esp_session: 1, event: null });
  s = reduce(s, { type: 'phase_update', phase: null, event: { name: 'CONTACT_SHIFT', detail: 'RESPONSE:722' } });
  s = reduce(s, { type: 'baseline_result', fsr: { mean: 2205.7, sd: 62.98 }, gsr: { mean: 2251.8, sd: 12.08 } });
  s = reduce(s, result());
  s = reduce(s, result()); // 재연결 후 서버가 같은 결과를 다시 보내도 한 번만
  s = reduce(s, result({ trial_id: 2, decision: 'truth' }));
  assert.equal(s.status.input, 'replay'); assert.equal(s.phase.phase, 'RESPONSE');
  assert.deepEqual(s.events.map(e => e.name), ['CONTACT_SHIFT']);
  assert.equal(s.baseline.fsr.sd, 62.98);
  assert.equal(s.results.length, 2); assert.equal(latestResult(s).trial_id, 2);
  assert.equal(reduce(s, { type: 'unknown' }), s);
});

test('sample batches become a 20 s window and reset when device time jumps back', () => {
  const batch = (t0, n) => ({ type: 'sample_batch', time_ms: Array.from({ length: n }, (_, i) => t0 + i * 40),
    fsr: Array(n).fill(2000), gsr: Array(n).fill(2200), ir: Array(n).fill(100000), red: Array(n).fill(90000),
    phase: Array(n).fill('RESPONSE'), contact: Array(n).fill(1) });
  assert.deepEqual(batchToPoints(batch(0, 2))[1], { t: 40, fsr: 2000, gsr: 2200, ir: 100000, red: 90000, phase: 'RESPONSE', contact: 1 });
  let s = initialState();
  for (let k = 0; k < 30; k++) s = reduce(s, batch(k * 1000, 25)); // 30초 분량
  const span = s.samples.at(-1).t - s.samples[0].t;
  assert.ok(span <= WINDOW_MS && span > WINDOW_MS - 1000);
  s = reduce(s, batch(500, 5)); // 재부팅이나 재생 처음
  assert.equal(s.samples.length, 5);
  assert.equal(reduce(s, { type: 'sample_batch', time_ms: [] }), s);
});

test('normalize maps to 0..1 and handles flat or missing values', () => {
  assert.deepEqual(normalize([10, 20, 15]), [0, 1, 0.5]);
  assert.deepEqual(normalize([5, 5]), [0.5, 0.5]);
  assert.deepEqual(normalize([NaN, 1, 3]), [0.5, 0, 1]);
  assert.deepEqual(normalize([]), []);
});

test('phase steps and websocket url', () => {
  assert.equal(phaseStep('BASELINE'), 'baseline'); assert.equal(phaseStep('QUESTION'), 'question');
  assert.equal(phaseStep('RESPONSE'), 'question'); assert.equal(phaseStep('RESULT'), 'result'); assert.equal(phaseStep('ABORTED'), 'result');
  assert.equal(wsUrl({ protocol: 'http:', host: '127.0.0.1:8000' }), 'ws://127.0.0.1:8000/ws');
  assert.equal(wsUrl({ protocol: 'https:', host: 'x' }), 'wss://x/ws');
});

test('session label follows the server session index, not device session 0', () => {
  let s = initialState();
  assert.equal(sessionLabel(s.session), '세션 시작 전');
  s = reduce(s, { type: 'phase_update', phase: 'PLACE_HAND', esp_session: 0, session_index: null, session_active: false, event: null });
  assert.equal(sessionLabel(s.session), '세션 시작 전');
  s = reduce(s, { type: 'phase_update', phase: 'BASELINE', esp_session: 1, session_index: 2, session_active: true, event: null });
  assert.equal(sessionLabel(s.session), '세션 S02');
  // 세션이 끝난 뒤 기기는 PLACE_HAND를 세션 0으로 보내지만 표시는 S02 · 종료
  s = reduce(s, { type: 'phase_update', phase: 'PLACE_HAND', esp_session: 0, session_index: 2, session_active: false, event: null });
  assert.equal(sessionLabel(s.session), '세션 S02 · 종료');
  assert.ok(!sessionLabel(s.session).includes('0 ·') && !sessionLabel(s.session).includes('세션 0'));
});

test('replay and dummy hide the place-hand prompt and say the measurement follows the input', () => {
  const replay = phaseDisplay('PLACE_HAND', status('replay'));
  assert.equal(replay.pill, null);
  assert.equal(replay.title, '측정은 저장된 로그를 따라갑니다.');
  const dummy = phaseDisplay('PLACE_HAND', status('dummy'));
  assert.equal(dummy.pill, null); assert.equal(dummy.title, '측정은 합성 신호를 따라갑니다.');
  assert.equal(phaseDisplay(null, status('replay')).title, '측정은 저장된 로그를 따라갑니다.');
  for (const v of [replay, dummy]) assert.ok(!`${v.title}${v.desc}`.includes('손을 올려'));
  // 다른 단계는 그대로
  assert.equal(phaseDisplay('RESPONSE', status('replay')).pill, '답변 측정 중');
  // 실제 기기에서는 손 올리기 안내를 보여 준다
  const device = phaseDisplay('PLACE_HAND', status('serial', { live: true }));
  assert.equal(device.pill, '손 올리기'); assert.equal(device.title, '센서에 손을 올려 주세요.');
});

test('unknown warning bits are not shown on screen', () => {
  assert.deepEqual(visibleWarnings(['UNKNOWN_WARN_BITS=64', 'DEVICE_WARN=CONTACT_DIP']), ['DEVICE_WARN=CONTACT_DIP']);
  assert.deepEqual(visibleWarnings(undefined), []);
  const v = trialView(result({ quality: { ok: true, flags: [], warnings: ['UNKNOWN_WARN_BITS=64'] } }));
  assert.deepEqual(v.warnings, []);
});

// ---------- 연결과 자동 재연결 (가짜 WebSocket, 가짜 타이머) ----------
function fakes() {
  const sockets = [], timers = [];
  class FakeWS { constructor(url) { this.url = url; sockets.push(this); } close() { this.closed = true; } }
  return { sockets, timers, FakeWS, setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {} };
}

test('first connection failure keeps demo mode (no retry)', () => {
  const f = fakes(); const states = [];
  connectLive({ url: 'ws://x/ws', WebSocketImpl: f.FakeWS, setTimer: f.setTimer, clearTimer: f.clearTimer, onState: s => states.push(s) });
  f.sockets[0].onclose();
  assert.deepEqual(states, ['connecting', 'unavailable']);
  assert.equal(f.timers.length, 0);
});

test('after a disconnect it reports it and reconnects with backoff', () => {
  const f = fakes(); const states = []; const msgs = [];
  connectLive({ url: 'ws://x/ws', WebSocketImpl: f.FakeWS, setTimer: f.setTimer, clearTimer: f.clearTimer,
    onState: (s, info) => states.push([s, info?.reconnected ?? info?.retryIn ?? null]), onMessage: m => msgs.push(m) });
  f.sockets[0].onopen();
  f.sockets[0].onmessage({ data: '{"type":"device_status","input":"dummy"}' });
  f.sockets[0].onmessage({ data: 'not json' }); // 무시
  f.sockets[0].onclose();
  assert.deepEqual(f.timers.map(t => t.ms), [1000]);
  f.timers[0].fn(); f.sockets[1].onclose();      // 재연결 실패 → 2초 뒤
  f.timers[1].fn(); f.sockets[2].onopen();       // 재연결 성공
  f.sockets[2].onclose();                        // 다시 끊기면 1초부터
  assert.deepEqual(f.timers.map(t => t.ms), [1000, 2000, 1000]);
  assert.deepEqual(states, [['connecting', null], ['open', false], ['disconnected', 1000], ['reconnecting', null],
    ['disconnected', 2000], ['reconnecting', null], ['open', true], ['disconnected', 1000]]);
  assert.deepEqual(msgs, [{ type: 'device_status', input: 'dummy' }]);
});

test('close stops reconnecting', () => {
  const f = fakes();
  const conn = connectLive({ url: 'ws://x/ws', WebSocketImpl: f.FakeWS, setTimer: f.setTimer, clearTimer: f.clearTimer });
  f.sockets[0].onopen(); conn.close(); f.sockets[0].onclose();
  assert.equal(f.timers.length, 0); assert.equal(f.sockets[0].closed, true);
});
