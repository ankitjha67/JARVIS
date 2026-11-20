// Auto-send after dictation (FR-V2). Wispr Flow inserts a whole utterance at once, which looks like a
// burst of characters arriving in a single input event; typing arrives one character at a time.

export const BURST_MIN_CHARS = 8;

export type BurstVerdict = 'arm' | 'disarm' | 'keep';

/** Classify one input event from the change in text length. */
export function classifyInput(prevLength: number, nextLength: number, inputType: string | undefined): BurstVerdict {
  const delta = nextLength - prevLength;
  if (nextLength === 0) return 'disarm';
  // A manual paste is deliberate editing, not dictation: never auto-send it.
  if (inputType === 'insertFromPaste' || inputType === 'insertFromDrop') return 'disarm';
  if (delta >= BURST_MIN_CHARS) return 'arm';
  if (delta <= 0 || delta === 1) return 'disarm'; // typing or deleting means the user is editing
  return 'keep';
}
