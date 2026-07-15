import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  buildRosterRows,
  filterRoster,
  mergePlayerDetail,
  type RosterRow,
} from './roster';
import type { BalanceOut, PlayerHistory, PlayerOut } from './types';

const nameArb = fc.string({ minLength: 0, maxLength: 12 });

const playerArb: fc.Arbitrary<PlayerOut> = fc.record({
  id: fc.uuid(),
  first_name: nameArb,
  last_name: fc.option(nameArb, { nil: null }),
  birth_date: fc.constant(null),
  grade: fc.constant(null),
  school_id: fc.constant(null),
  location_id: fc.constant('loc-1'),
  consent_status: fc.constant('complete'),
  active: fc.constant(true),
});

describe('Players roster search (Property 13)', () => {
  // Feature: revenue-pwa, Property 13: Roster search returns exactly the name matches.
  // For any cached roster and any search text, the displayed players are exactly those
  // whose name matches the search text, and each displayed row contains name, entitlement
  // balance, last-visit date, and entitlement status.
  // Validates: Requirements 9.1, 9.4
  it('Property 13: filtered rows are exactly the case-insensitive name matches', () => {
    fc.assert(
      fc.property(fc.array(playerArb, { maxLength: 30 }), fc.string({ maxLength: 6 }), (players, search) => {
        const rows = buildRosterRows({ players });

        // Every row carries the four required display fields (Req 9.1).
        for (const row of rows) {
          expect(row).toHaveProperty('name');
          expect(row).toHaveProperty('balance');
          expect(row).toHaveProperty('lastVisit');
          expect(row).toHaveProperty('entitlementStatus');
        }

        const visible = filterRoster(rows, search);
        const needle = search.trim().toLowerCase();
        const expected: RosterRow[] =
          needle === '' ? rows : rows.filter((r) => r.name.toLowerCase().includes(needle));

        expect(visible.map((r) => r.id).sort()).toEqual(expected.map((r) => r.id).sort());
        // No non-matching row leaks through.
        if (needle !== '') {
          for (const row of visible) {
            expect(row.name.toLowerCase().includes(needle)).toBe(true);
          }
        }
        return true;
      }),
      { numRuns: 100 },
    );
  });
});

describe('Player detail merge (Property 14)', () => {
  // Feature: revenue-pwa, Property 14: Player detail merges server history with local
  // unsynced records. For any server history and any set of locally captured, not-yet-
  // synced records for a player, the detail view contains every server record and every
  // local unsynced record for that player (union, no omission).
  // Validates: Requirements 9.3
  const recArb = fc.array(fc.record({ id: fc.uuid() }), { maxLength: 15 });

  it('Property 14: merged detail is the union of server + local records', () => {
    fc.assert(
      fc.property(
        recArb,
        recArb,
        recArb,
        recArb,
        recArb,
        recArb,
        (srvS, srvP, srvD, locS, locP, locD) => {
          const server: PlayerHistory = {
            player_id: 'p1',
            sessions: srvS,
            payments: srvP,
            entitlement_draws: srvD,
          };
          const merged = mergePlayerDetail(server, {
            sessions: locS,
            payments: locP,
            entitlement_draws: locD,
          });

          expect(merged.sessions).toHaveLength(srvS.length + locS.length);
          expect(merged.payments).toHaveLength(srvP.length + locP.length);
          expect(merged.entitlement_draws).toHaveLength(srvD.length + locD.length);

          // Every server record and every local record is present (no omission).
          for (const r of [...srvS, ...locS]) expect(merged.sessions).toContain(r);
          for (const r of [...srvP, ...locP]) expect(merged.payments).toContain(r);
          for (const r of [...srvD, ...locD]) expect(merged.entitlement_draws).toContain(r);
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });

  it('merges into a local-only view when server history is empty (offline)', () => {
    const server: PlayerHistory = { player_id: 'p1', sessions: [], payments: [], entitlement_draws: [] };
    const merged = mergePlayerDetail(server, { sessions: [{ id: 'x' } as unknown as Record<string, unknown>] });
    expect(merged.sessions).toHaveLength(1);
  });

  // fast-check needs BalanceOut to be referenced somewhere for the roster balance path.
  it('summarises balances into a display figure', () => {
    const balances: BalanceOut[] = [
      { entitlement_id: 'e1', product_id: 'pr1', remaining_units: 30, valid_from: null, valid_to: null, status: 'active' },
      { entitlement_id: 'e2', product_id: 'pr2', remaining_units: 60, valid_from: null, valid_to: null, status: 'active' },
    ];
    const rows = buildRosterRows({
      players: [
        {
          id: 'p1',
          first_name: 'Ada',
          last_name: null,
          birth_date: null,
          grade: null,
          school_id: null,
          location_id: 'loc-1',
          consent_status: 'complete',
          active: true,
        },
      ],
      balancesByPlayer: { p1: balances },
    });
    expect(rows[0].balance).toBe(90);
    expect(rows[0].entitlementStatus).toBe('active');
  });
});
