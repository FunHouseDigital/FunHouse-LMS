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
    productsRequired,
    lastRefreshedAt,
    refresh,
  } = useReferenceData();

  if (!isAuthenticated || status === 'idle') return null;

  const refreshedLabel = formatTimestamp(lastRefreshedAt);
  const allRequiredAvailable = playersAvailable && (!productsRequired || productsAvailable);
  const dataLabel = productsRequired ? 'players and products' : 'learners';

  return (
    <aside aria-label="Reference data status" data-reference-data-status={status}>
      {status === 'loading' && (
        <p role="status">
          {playersAvailable || (productsRequired && productsAvailable)
            ? `Refreshing ${dataLabel}… Saved data remains available.`
            : `Loading ${dataLabel}…`}
        </p>
      )}

      {status === 'ready' && (
        <p role="status">
          {dataLabel[0].toUpperCase() + dataLabel.slice(1)} are available offline
          {refreshedLabel ? ` — updated ${refreshedLabel}` : ''}.
        </p>
      )}

      {status === 'offline' && (
        <p role="status">
          {allRequiredAvailable
            ? `You are offline. Using saved ${dataLabel}.`
            : `You are offline. Connect to the internet to load ${dataLabel}.`}
        </p>
      )}

      {status === 'error' && (
        <p role="alert">
          {allRequiredAvailable
            ? `The latest ${dataLabel} could not be confirmed. Saved data is still available.`
            : `${dataLabel[0].toUpperCase() + dataLabel.slice(1)} could not be loaded. Check your connection and try again.`}
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
