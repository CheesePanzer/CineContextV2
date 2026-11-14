import {useEffect, useState} from 'react';
import {api} from '../api/client.js';

export const TERMINAL_STATUSES = new Set(['completed', 'partial', 'failed', 'interrupted']);

export function useRunPolling(runId, poll = true, revision = 0) {
  const [run, setRun] = useState(null);
  const [error, setError] = useState('');
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let active = true;
    let timer;
    setRun(null);
    setError('');
    setUnavailable(false);
    async function read(delay = 2000) {
      try {
        const result = await api.getRun(runId);
        if (!active) return;
        setRun(result);
        setError('');
        if (poll && !TERMINAL_STATUSES.has(result.status)) timer = setTimeout(() => read(2000), 2000);
      } catch (problem) {
        if (!active) return;
        setError(problem.message);
        if ([404, 422].includes(problem.status)) setUnavailable(true);
        else if (poll) {
          const nextDelay = Math.min(delay * 2, 10000);
          timer = setTimeout(() => read(nextDelay), nextDelay);
        }
      }
    }
    read();
    return () => { active = false; clearTimeout(timer); };
  }, [runId, poll, revision]);

  return {run, error, unavailable};
}
