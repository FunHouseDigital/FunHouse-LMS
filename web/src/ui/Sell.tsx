/**
 * Sell screen — `Sell_Module` (Req 12). See design.md "Sell — Sell_Module".
 *
 * Product options: pay-per-use cash, a catalog-priced subscription (up to four
 * members — a fifth is prevented), and the approved R250 Holiday Special pass.
 * Prices come from
 * the cached `GET /products`. On complete the pure builder produces a `payment`
 * record + action and, for subscription / Holiday Special, an `entitlement`
 * create action per included member; the commit step persists and enqueues them
 * offline (Req 12.5).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useServices } from '../state/servicesState';
import { useReferenceData } from '../state/referenceDataState';
import { getCachedRead } from '../store/localStore';
import {
  MAX_SUBSCRIPTION_MEMBERS,
  addSubscriptionMember,
  buildSellActions,
  canAddSubscriptionMember,
  type SellInput,
  type SellKind,
} from '../domain/captures/sell';
import type { ProductOut } from '../domain/types';
import { centsToRand } from '../domain/revenue';
import { useKnownPlayersState } from './useKnownPlayers';

/** Approved Holiday Special sale price. Stale catalog values must fail closed. */
export const HOLIDAY_SPECIAL_PRICE_CENTS = 25_000;

const KIND_LABELS: Record<SellKind, string> = {
  pay_per_use: 'Pay per use',
  subscription: 'New subscription',
  holiday_special: 'Holiday Special',
};

function isHolidaySpecialName(name: unknown): boolean {
  return name === 'Holiday Special';
}

function matchProduct(products: ProductOut[], kind: SellKind): ProductOut | undefined {
  if (kind === 'subscription') return products.find((p) => p.type === 'subscription');
  if (kind === 'holiday_special') {
    return products.find(
      (p) => p.type === 'once_off_pass' && isHolidaySpecialName(p.name),
    );
  }
  return products.find((p) => p.type === 'pay_per_use');
}

function isApprovedCatalogProduct(
  product: ProductOut | undefined,
  kind: Exclude<SellKind, 'pay_per_use'>,
): product is ProductOut {
  if (!product || !Number.isFinite(product.price_cents) || product.price_cents <= 0) {
    return false;
  }
  if (kind === 'subscription') return product.type === 'subscription';
  return (
    product.type === 'once_off_pass' &&
    isHolidaySpecialName(product.name) &&
    product.price_cents === HOLIDAY_SPECIAL_PRICE_CENTS
  );
}

function parseRandToCents(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === '') return null;

  const rand = Number(trimmed);
  if (!Number.isFinite(rand) || rand <= 0) return null;

  const cents = Math.round(rand * 100);
  return Number.isSafeInteger(cents) && cents > 0 ? cents : null;
}

export function Sell() {
  const { commit } = useServices();
  const { revision, productsCacheKey } = useReferenceData();
  const {
    players,
    loaded: playersLoaded,
    error: playersError,
  } = useKnownPlayersState();
  const [products, setProducts] = useState<ProductOut[]>([]);
  const [kind, setKind] = useState<SellKind>('pay_per_use');
  const [playerId, setPlayerId] = useState<string | null>(null);
  const [members, setMembers] = useState<string[]>([]);
  const [cashRand, setCashRand] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const cachedProducts = await getCachedRead<ProductOut[]>(productsCacheKey);
      if (alive) setProducts(cachedProducts?.data ?? []);
    })();
    return () => {
      alive = false;
    };
  }, [productsCacheKey, revision]);

  const product = useMemo(() => matchProduct(products, kind), [products, kind]);

  const priceCents = useMemo(() => {
    if (kind === 'pay_per_use') return parseRandToCents(cashRand);
    return isApprovedCatalogProduct(product, kind) ? product.price_cents : null;
  }, [kind, product, cashRand]);

  const canComplete = Boolean(playerId && priceCents !== null);

  const onComplete = useCallback(async () => {
    if (!playerId || priceCents === null) return;
    if (kind !== 'pay_per_use' && !isApprovedCatalogProduct(product, kind)) return;
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
      <p className="screen-intro">Record a payment and issue the matching pass or subscription.</p>

      <fieldset className="product-options">
        <legend>Product</legend>
        {(Object.keys(KIND_LABELS) as SellKind[]).map((k) => {
          const optionProduct = matchProduct(products, k);
          const price = k === 'pay_per_use'
            ? 'Enter amount'
            : isApprovedCatalogProduct(optionProduct, k)
              ? centsToRand(optionProduct.price_cents)
              : 'Unavailable';
          return (
            <label key={k}>
              <input
                type="radio"
                name="sell-kind"
                value={k}
                checked={kind === k}
                onChange={() => {
                  setKind(k);
                  setSaved(false);
                }}
              />
              <span className="product-option-title">{KIND_LABELS[k]}</span>
              <span className="product-price">{price}</span>
              <span className="product-note">
                {k === 'pay_per_use'
                  ? 'Cash amount entered at checkout'
                  : k === 'subscription'
                    ? `Up to ${MAX_SUBSCRIPTION_MEMBERS} members`
                    : '3 hours per week · Sunday reset'}
              </span>
            </label>
          );
        })}
      </fieldset>

      {kind !== 'pay_per_use' && !isApprovedCatalogProduct(product, kind) && (
        <p role="alert">Price unavailable. Refresh data before completing this sale.</p>
      )}

      <fieldset>
        <legend>Player</legend>
        {!playersLoaded && <p role="status">Loading players…</p>}
        {playersLoaded && playersError && (
          <p role="alert">Players unavailable. Refresh data and try again.</p>
        )}
        {playersLoaded && !playersError && players.length === 0 && (
          <p role="status">No players are available yet.</p>
        )}
        <ul aria-label="Players">
          {players.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                aria-pressed={playerId === p.id}
                onClick={() => {
                  setPlayerId(p.id);
                  setSaved(false);
                }}
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
            min="0.01"
            step="0.01"
            aria-label="Cash amount"
            value={cashRand}
            onChange={(e) => {
              setCashRand(e.target.value);
              setSaved(false);
            }}
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
                    onClick={() => {
                      setSaved(false);
                      setMembers((prev) =>
                        prev.includes(p.id)
                          ? prev.filter((m) => m !== p.id)
                          : addSubscriptionMember(prev, p.id),
                      );
                    }}
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
