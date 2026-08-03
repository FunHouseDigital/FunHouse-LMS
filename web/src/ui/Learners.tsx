import { useEffect, useMemo, useState } from 'react';
import { playerName } from '../domain/roster';
import { useReferenceData } from '../state/referenceDataState';
import { getCachedRead } from '../store/localStore';
import type { PlayerOut } from '../domain/types';

interface LearnerRow {
  id: string;
  name: string;
  grade: string | null;
}

/** School-scoped, read-only learner roster for facilitators. */
export function Learners() {
  const { playersCacheKey, revision } = useReferenceData();
  const [learners, setLearners] = useState<LearnerRow[]>([]);
  const [search, setSearch] = useState('');
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const cached = await getCachedRead<PlayerOut[]>(playersCacheKey);
      if (!alive) return;
      setLearners(
        (cached?.data ?? []).map((player) => ({
          id: player.id,
          name: playerName(player),
          grade: player.grade,
        })),
      );
      setLoaded(true);
    })();
    return () => {
      alive = false;
    };
  }, [playersCacheKey, revision]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return needle === ''
      ? learners
      : learners.filter((learner) => learner.name.toLowerCase().includes(needle));
  }, [learners, search]);

  return (
    <section aria-label="Learners" data-screen-body="learners">
      <h1>Learners</h1>
      <p>Only learners assigned to your school are shown.</p>
      <label>
        Search learners
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>

      {loaded && learners.length === 0 && <p>No learners are assigned to this school.</p>}
      <ul aria-label="Learner roster">
        {visible.map((learner) => (
          <li key={learner.id}>
            <strong>{learner.name}</strong>
            {learner.grade ? ` — Grade ${learner.grade}` : ''}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default Learners;
