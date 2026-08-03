/**
 * Sell screen — `Sell_Module` (Req 12). See design.md "Sell — Sell_Module".
 *
 * Product options: pay-per-use cash, a new subscription (R350, up to four
 * members — a fifth is prevented), and a Holiday Special pass. Prices come from
 * the cached `GET /products`. On complete the pure builder produces a `payment`
 * record + action and, for subscription / Holiday Special, an `entitlement`
 * create action per included member; the commit step persists and enqueues them
 * offline (Req 12.5).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useServices } from '../state/servicesState';
import { useReferenceData } from '../state/referenceDataState';
import { getAllLocalRecords, getCachedRead } from '../store/localStore';
import {
  MAX_SUBSCRIPTION_MEMBERS,
  SUBSCRIPTION_PRICE_CENTS,
  addSubscriptionMember,
  buildSellActions,
  canAddSubscriptionMember,
  type SellInput,
  type SellKind,
} from '../domain/captures/sell';
import { playerName } from '../domain/roster';
import type { PlayerOut, ProductOut } from '../domain/types';

interface PlayerChoice {
  id: string;
  name: string;
}

const KIND_LABELS: Record<SellKind, string> = {
  pay_per_use: 'Pay per use',
  subscription: 'New subscription',
  holiday_special: 'Holiday Special',
};

function matchProduct(products: ProductOut[], kind: SellKind): ProductOut | undefined {
  if (kind === 'subscription') return products.find((p) => p.type === 'subscription');
  if (kind === 'holiday_special')
    return products.find((p) => p.type === 'holiday_special' || /holiday/i.test(p.name));
  return products.find((p) => p.type === 'pay_per_use');
}

export function Sell() {
  const { commit } = useServices();
  const { revision, playersCacheKey, productsCacheKey } = useReferenceData();
  const [products, setProducts] = useState<ProductOut[]>([]);
  const [players, setPlayers] = useState<PlayerChoice[]>([]);
  const [kind, setKind] = useState<SellKind>('pay_per_use');
  const [playerId, setPlayerId] = useState<string | null>(null);
  const [members, setMembers] = useState<string[]>([]);
  const [cashRand, setCashRand] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const cachedProducts = await getCachedRead<ProductOut[]>(productsCacheKey);
      const cachedPlayers = await getCachedRead<PlayerOut[]>(playersCacheKey);
      const roster: PlayerChoice[] = (cachedPlayers?.data ?? []).map((p) => ({
        id: p.id,
        name: playerName(p),
      }));
      const local = (await getAllLocalRecords('players')).map((r) => ({
        id: String(r.local_id),
        name: String(r.name ?? 'New player'),
      }));
      if (alive) {
        setProducts(cachedProducts?.data ?? []);
        setPlayers([...roster, ...local.filter((l) => !roster.some((r) => r.id === l.id))]);
      }
    })();
    return () => {
      alive = false;
    };
  }, [playersCacheKey, productsCacheKey, revision]);

  const product = useMemo(() => matchProduct(products, kind), [products, kind]);

  const priceCents = useMemo(() => {
    if (kind === 'subscription') return product?.price_cents ?? SUBSCRIPTION_PRICE_CENTS;
    if (kind === 'holiday_special') return product?.price_cents ?? 0;
    const rand = Number(cashRand.trim());
    return Number.isFinite(rand) && rand >= 0 ? Math.round(rand * 100) : 0;
  }, [kind, product, cashRand]);

  const canComplete = useMemo(() => {
    if (!playerId) return false;
    if (kind === 'pay_per_use') {
      const rand = Number(cashRand.trim());
      return Number.isFinite(rand) && rand >= 0;
    }
    return true;
  }, [playerId, kind, cashRand]);

  const onComplete = useCallback(async () => {
    if (!playerId) return;
    const input: SellInput = {
      kind,
      playerId,
      priceCents,
      productId: kind === 'pay_per_use' ? undefined : product?.id,
      memberIds: kind === 'subscription' ? (members.length > 0 ? members : [playerId]) : undefined,
    };
    const result = buildSellActions(input, {
      now: new Date().toISOString(),
      newId: () => globalThis.crypto.randomUUID(),
    });
    await commit(result);
    setSaved(true);
    setMembers([]);
    setCashRand('');
  }, [playerId, kind, priceCents, product, members, commit]);

  return (
    <section aria-label="Sell" data-screen-body="sell">
      <h1>Sell</h1>

      <fieldset>
        <legend>Product</legend>
        {(Object.keys(KIND_LABELS) as SellKind[]).map((k) => (
          <label key={k}>
            <input
              type="radio"
              name="sell-kind"
              value={k}
              checked={kind === k}
              onChange={() => setKind(k)}
            />
            {KIND_LABELS[k]}
            {k === 'subscription' ? ` (R${(SUBSCRIPTION_PRICE_CENTS / 100).toFixed(0)})` : ''}
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend>Player</legend>
        <ul aria-label="Players">
          {players.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                aria-pressed={playerId === p.id}
                onClick={() => setPlayerId(p.id)}
              >
                {p.name}
              </button>
            </li>
          ))}
        </ul>
      </fieldset>

      {kind === 'pay_per_use' && (
        <label>
          Amount (R)
          <input
            type="number"
            min={0}
            step="0.01"
            aria-label="Cash amount"
            value={cashRand}
            onChange={(e) => setCashRand(e.target.value)}
          />
        </label>
      )}

      {kind === 'subscription' && (
        <fieldset>
          <legend>Members (up to {MAX_SUBSCRIPTION_MEMBERS})</legend>
          <ul aria-label="Subscription members">
            {players.map((p) => {
              const included = members.includes(p.id);
              const blocked = !included && !canAddSubscriptionMember(members);
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    aria-pressed={included}
                    disabled={blocked}
                    onClick={() =>
                      setMembers((prev) =>
                        prev.includes(p.id)
                          ? prev.filter((m) => m !== p.id)
                          : addSubscriptionMember(prev, p.id),
                      )
                    }
                  >
                    {p.name}
                  </button>
                </li>
              );
            })}
          </ul>
          <p data-field="member-count">
            {members.length} / {MAX_SUBSCRIPTION_MEMBERS} members
          </p>
        </fieldset>
      )}

      <button type="button" disabled={!canComplete} onClick={onComplete}>
        Complete sale
      </button>

      {saved && <p role="status">Sale recorded</p>}
    </section>
  );
}

export default Sell;
