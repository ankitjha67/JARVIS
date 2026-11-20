import { describe, expect, it } from 'vitest';
import { detectProvider, isValidName, parsePairs, splitCommand } from './text';
import { classifyInput } from './dictation';
import type { ServerMessage } from './protocol';
import { initialState, reduce, type HudState } from './store';

const base = { ...initialState, setView() {}, setConn() {}, setAuditLoading() {}, apply() {} } as unknown as HudState;
const run = (msgs: ServerMessage[], s: HudState = base) => msgs.reduce((acc, m) => ({ ...acc, ...reduce(acc, m) }), s);

describe('dictation burst detector', () => {
  it('arms on a dictated burst', () => expect(classifyInput(0, 24, 'insertText')).toBe('arm'));
  it('disarms while typing or deleting', () => {
    expect(classifyInput(24, 25, 'insertText')).toBe('disarm');
    expect(classifyInput(24, 20, 'deleteContentBackward')).toBe('disarm');
  });
  it('never auto-sends a manual paste', () => expect(classifyInput(0, 500, 'insertFromPaste')).toBe('disarm'));
  it('disarms when the field is emptied', () => expect(classifyInput(30, 0, undefined)).toBe('disarm'));
});

describe('MCP command parsing', () => {
  it('splits words and keeps quoted paths together', () => {
    expect(splitCommand('npx -y @scope/server "C:\\My Files\\data" --flag')).toEqual(['npx', '-y', '@scope/server', 'C:\\My Files\\data', '--flag']);
  });
});

describe('key handling helpers', () => {
  it('recognises provider keys by prefix, most specific first', () => {
    expect(detectProvider('sk-ant-api03-abcdefghijklmnop')).toBe('anthropic');
    expect(detectProvider('sk-or-v1-abcdefghijklmnop')).toBe('openrouter');
    expect(detectProvider('sk-proj-abcdefghijklmnopqrstuv')).toBe('openai');
    expect(detectProvider('nvapi-abcdefghijklmnop')).toBe('nvidia');
    expect(detectProvider('gsk_abcdefghijklmnop')).toBe('groq');
    expect(detectProvider('AIzaSyA1234567890abcdefghijklmnop')).toBe('gemini');
    expect(detectProvider('just-some-text')).toBeNull();
  });
  it('validates vault and provider names', () => {
    expect(isValidName('github_token')).toBe(true);
    for (const bad of ['Github', '1abc', 'has space', 'a-b', '']) expect(isValidName(bad)).toBe(false);
  });
  it('parses NAME=value lines and rejects malformed ones', () => {
    expect(parsePairs('Authorization=Bearer ${secret:x}\n\nX-Api-Key: abc=def')).toEqual({ Authorization: 'Bearer ${secret:x}', 'X-Api-Key': 'abc=def' });
    expect(parsePairs('')).toEqual({});
    expect(parsePairs('no separator here')).toBeNull();
    expect(parsePairs('bad name=1')).toBeNull();
  });
});

describe('store reducer', () => {
  it('streams an assistant turn and marks it for speech', () => {
    const s = run([
      { type: 'user.echo', text: 'hi', run_id: 'r' },
      { type: 'assistant.start', run_id: 'r', provider: 'x', provider_label: 'X', model: 'm' },
      { type: 'assistant.delta', run_id: 'r', text: 'Hel' },
      { type: 'assistant.delta', run_id: 'r', text: 'lo' },
      { type: 'assistant.done', run_id: 'r', cancelled: false, has_tools: false },
    ]);
    expect(s.transcript.map((t) => t.kind)).toEqual(['user', 'assistant']);
    expect(s.transcript[1]).toMatchObject({ text: 'Hello', streaming: false });
    expect(s.lastSpoken?.text).toBe('Hello');
  });

  it('drops an empty tool-only turn', () => {
    const s = run([
      { type: 'assistant.start', run_id: 'r', provider: 'x', provider_label: 'X', model: 'm' },
      { type: 'assistant.done', run_id: 'r', cancelled: false, has_tools: true },
    ]);
    expect(s.transcript).toHaveLength(0);
  });

  it('tracks a tool call through approval to completion', () => {
    const approval = { approval_id: 'a', run_id: 'r', call_id: 'c', tool: 'shell_run', summary: 'Run', args: {}, tier: 2 as const, reason: 'x', can_remember: true, timeout_s: 120 };
    let s = run([
      { type: 'tool.start', run_id: 'r', call_id: 'c', tool: 'shell_run', tier: 2, summary: 'Run', args: {} },
      { type: 'approval.request', ...approval },
    ]);
    expect(s.activity[0].status).toBe('waiting');
    expect(s.approvals).toHaveLength(1);
    s = run([{ type: 'approval.resolved', approval_id: 'a', decision: 'allow' }, { type: 'tool.end', call_id: 'c', status: 'ok', duration_ms: 12, error: null }], s);
    expect(s.approvals).toHaveLength(0);
    expect(s.activity[0]).toMatchObject({ status: 'ok', durationMs: 12 });
    expect(s.pulse).toBe(1);
  });

  it('closes a streaming bubble and shows the error', () => {
    const s = run([
      { type: 'assistant.start', run_id: 'r', provider: 'x', provider_label: 'X', model: 'm' },
      { type: 'error', message: 'down', code: 'provider' },
    ]);
    expect(s.transcript).toEqual([expect.objectContaining({ kind: 'notice', level: 'error', text: 'down' })]);
  });

  it('opens a listening session on the wake word and turns the finished request into a command', () => {
    let s = run([{ type: 'voice.session', state: 'listening', text: '' }]);
    expect(s.voice.active).toBe(true);
    s = run([{ type: 'voice.level', level: 0.5 }, { type: 'voice.session', state: 'listening', text: 'open the' }], s);
    expect(s.voice).toMatchObject({ active: true, text: 'open the', level: 0.5 });
    s = run([{ type: 'voice.session', state: 'final', text: 'open the calculator' }], s);
    expect(s.voice.active).toBe(false);
    expect(s.voiceCommand).toMatchObject({ text: 'open the calculator' });
  });

  it('closes the listening session quietly when nothing was asked, and ignores stray levels', () => {
    let s = run([{ type: 'voice.session', state: 'listening', text: '' }, { type: 'voice.session', state: 'ended', text: '' }]);
    expect(s.voice.active).toBe(false);
    expect(s.voiceCommand).toBeNull();
    s = run([{ type: 'voice.level', level: 0.9 }], s);
    expect(s.voice.level).toBe(0);
  });

  it('keeps 60 telemetry samples', () => {
    const t = { type: 'telemetry' as const, cpu_percent: 5, ram_percent: 6, ram_used_gb: 1, ram_total_gb: 16, gpu: null, disks: [], net_down_kbps: 0, net_up_kbps: 0, battery: null, top: [] };
    const s = run(Array.from({ length: 70 }, () => t));
    expect(s.series.cpu).toHaveLength(60);
  });
});
