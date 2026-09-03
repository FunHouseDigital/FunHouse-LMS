import { useEffect, useMemo, useState } from 'react';
import { localDataLifecycleIdentity } from '../domain/personalData';
import { playerName } from '../domain/roster';
import { useReferenceData } from '../state/referenceDataState';
import { getCachedRead } from '../store/localStore';
import type { PlayerOut } from '../domain/types';

interface LearnerRow { id: string; name: string; grade: string | null }
interface LearnerSnapshot { identity: string | null; rows: LearnerRow[]; loaded: boolean }

/** School-scoped, read-only learner roster for facilitators. */
export function Learners() {
  const { playersCacheKey, revision, owner } = useReferenceData();
  const identity = localDataLifecycleIdentity(owner);
  const [snapshot, setSnapshot] = useState<LearnerSnapshot>({ identity: null, rows: [], loaded: false });
  const [searchState, setSearchState] = useState({ identity: null as string | null, value: '' });

  useEffect(() => {
    let alive = true;
    void (async () => {
      if (!owner || !owner.isCurrent()) return;
      try {
        const cached = await getCachedRead<PlayerOut[]>(playersCacheKey, owner);
        if (!alive || !owner.isCurrent()) return;
        setSnapshot({
          identity,
          rows: (cached?.data ?? []).map((player) => ({ id: player.id, name: playerName(player), grade: player.grade })),
          loaded: true,
        });
      } catch {
        if (alive && owner.isCurrent()) setSnapshot({ identity, rows: [], loaded: true });
      }
    })();
    return () => { alive = false; };
  }, [identity, owner, playersCacheKey, revision]);

  const current = snapshot.identity === identity && owner?.isCurrent()
    ? snapshot
    : { identity, rows: [], loaded: false };
  const search = searchState.identity === identity ? searchState.value : '';
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return needle === '' ? current.rows : current.rows.filter((learner) => learner.name.toLowerCase().includes(needle));
  }, [current.rows, search]);

  return (
    <section aria-label="Learners" data-screen-body="learners">
      <h1>Learners</h1>
      <p className="screen-intro">Only learners assigned to your school are shown.</p>
      <label>
        Search learners
        <input type="search" value={search} onChange={(event) => setSearchState({ identity, value: event.target.value })} />
      </label>
      {current.loaded && current.rows.length === 0 && <p role="status">No learners are assigned to this school.</p>}
      {!current.loaded && <p role="status">Loading learners…</p>}
      {current.loaded && current.rows.length > 0 && visible.length === 0 && <p role="status">No learners match “{search.trim()}”.</p>}
      <ul aria-label="Learner roster">
        {visible.map((learner) => <li key={learner.id}><strong>{learner.name}</strong>{learner.grade ? ` — Grade ${learner.grade}` : ''}</li>)}
      </ul>
    </section>
  );
}

export default Learners;
