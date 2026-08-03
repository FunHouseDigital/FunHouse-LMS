import { useAuth } from '../state/authState';
import { useReferenceData } from '../state/referenceDataState';

function formatTimestamp(timestamp: string | null): string | null {
  if (!timestamp) return null;
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString();
}

export function ReferenceDataStatus() {
  const { isAuthenticated } = useAuth();
  const {
    status,
    playersAvailable,
    productsAvailable,
    lastRefreshedAt,
    refresh,
  } = useReferenceData();

  if (!isAuthenticated || status === 'idle') return null;

  const refreshedLabel = formatTimestamp(lastRefreshedAt);

  return (
    <aside aria-label="Player and product data status" data-reference-data-status={status}>
      {status === 'loading' && (
        <p role="status">
          {playersAvailable || productsAvailable
            ? 'Refreshing players and products… Saved data remains available.'
            : 'Loading players and products…'}
        </p>
      )}

      {status === 'ready' && (
        <p role="status">
          Players and products are available offline
          {refreshedLabel ? ` — updated ${refreshedLabel}` : ''}.
        </p>
      )}

      {status === 'offline' && (
        <p role="status">
          {playersAvailable && productsAvailable
            ? 'You are offline. Using saved players and products.'
            : playersAvailable
              ? 'You are offline. Saved players are available, but products have not been downloaded.'
              : productsAvailable
                ? 'You are offline. Saved products are available, but players have not been downloaded.'
                : 'You are offline. Connect to the internet to load players and products.'}
        </p>
      )}

      {status === 'error' && (
        <p role="alert">
          {playersAvailable && productsAvailable
            ? 'The latest data could not be confirmed. Saved players and products are still available.'
            : playersAvailable
              ? 'Products could not be loaded. Saved players are still available.'
              : productsAvailable
                ? 'Players could not be loaded. Saved products are still available.'
                : 'Players and products could not be loaded. Check your connection and try again.'}
        </p>
      )}

      {status !== 'loading' && (
        <button type="button" onClick={() => void refresh()}>
          Refresh data
        </button>
      )}
    </aside>
  );
}

export default ReferenceDataStatus;
