import { describe, expect, it } from 'vitest';

import { NAV_ITEMS } from './navigation';

describe('desktop navigation', () => {
  it('contains exactly the six required administration screens', () => {
    expect(NAV_ITEMS.map(item => item.label)).toEqual(['Dashboard', 'Providers', 'Routing', 'Requests', 'Playground', 'Settings']);
  });
});
