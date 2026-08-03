import { useEffect, useState } from 'react';
import { useAuth } from '../state/authState';
import { useServices } from '../state/servicesState';
import { useSyncStatus } from '../state/syncState';

function isOnline(): boolean {
  return typeof navigator === 'undefined' || navigator.onLine !== false;
}

function entityLabel(entity: string): string {
  return entity.replace(/_/g, ' ');
}

/** Shared, payload-free sync health and recovery surface for every role. */
export function SyncStatusSurface() {
  const { isAuthenticated } = useAuth();
  const {
    loading,
    unsyncedCount,
    blockedCount,
    quarantinedCount,
    stale,
    rejected,
  } = useSyncStatus();
  const {
    retrySync,
    syncing,
    lastAttemptOutcome,
    scopeAvailable,
  } = useServices();
  const [online, setOnline] = useState(isOnline);

  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  if (!isAuthenticated) return null;

  if (!scopeAvailable) {
    return (
      <section aria-label="Sync status">
        <p role="alert">Sync unavailable — log out and sign in again.</p>
      </section>
    );
  }

  let primary = loading
    ? 'Checking sync status…'
    : 'Up to date — no items waiting to sync.';
  if (!loading && syncing) {
    primary = `Syncing ${unsyncedCount} ${unsyncedCount === 1 ? 'item' : 'items'}…`;
  } else if (!loading && !online && unsyncedCount > 0) {
    primary = `Offline — ${unsyncedCount} ${unsyncedCount === 1 ? 'item is' : 'items are'} saved on this device and will sync when connected.`;
  } else if (!loading && unsyncedCount > 0) {
    primary = `${unsyncedCount} ${unsyncedCount === 1 ? 'item is' : 'items are'} waiting to sync.`;
  }

  return (
    <section aria-label="Sync status">
      <p role="status" data-field="sync-summary">{primary}</p>

      {lastAttemptOutcome === 'network-error' && unsyncedCount > 0 && (
        <p role="alert">
          Sync couldn’t connect. Your items are safe on this device.
        </p>
      )}

      {stale && (
        <p role="alert">This device hasn’t completed a sync in over 5 days.</p>
      )}

      {rejected.length > 0 && (
        <div role="alert">
          <p>{rejected.length} {rejected.length === 1 ? 'item needs' : 'items need'} attention.</p>
          <ul aria-label="Items needing attention">
            {rejected.map((item, index) => (
              <li key={`${item.entity}:${index}`}>
                {entityLabel(item.entity)}: {item.reason ?? 'The server did not provide a reason.'}
              </li>
            ))}
          </ul>
        </div>
      )}

      {blockedCount > 0 && (
        <p role="alert">
          {blockedCount} {blockedCount === 1 ? 'item is' : 'items are'} held and cannot be uploaded by this app version.
        </p>
      )}

      {quarantinedCount > 0 && (
        <p role="alert">
          {quarantinedCount} older offline {quarantinedCount === 1 ? 'item has' : 'items have'} an unknown owner and will not be uploaded. Contact support.
        </p>
      )}

      {unsyncedCount > 0 && (
        <button
          type="button"
          disabled={syncing || !online}
          onClick={() => void retrySync()}
        >
          Retry sync
        </button>
      )}
    </section>
  );
}

export default SyncStatusSurface;
