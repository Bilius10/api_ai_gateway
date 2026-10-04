import { describe, expect, it } from 'vitest';

import { LatestSelection } from './state';

describe('LatestSelection', () => {
  it('prevents an older request detail from replacing the latest selection', () => {
    const state = new LatestSelection();
    const oldToken = state.select('old');
    const newToken = state.select('new');
    expect(state.isCurrent('old', oldToken)).toBe(false);
    expect(state.isCurrent('new', newToken)).toBe(true);
  });

  it('distinguishes repeated requests for the same id', () => {
    const state = new LatestSelection();
    const first = state.select('same');
    const second = state.select('same');
    expect(state.isCurrent('same', first)).toBe(false);
    expect(state.isCurrent('same', second)).toBe(true);
  });
});
