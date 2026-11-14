import React, {useEffect, useState} from 'react';
import {Link, useNavigate, useOutletContext, useParams} from 'react-router-dom';
import {api} from '../api/client.js';
import Results from '../components/Results.jsx';
import {TERMINAL_STATUSES, useRunPolling} from '../hooks/useRunPolling.js';

export default function ResultPage() {
  const {runId} = useParams();
  const {run, error, unavailable} = useRunPolling(runId, false);
  const [retrying, setRetrying] = useState(false);
  const [actionError, setActionError] = useState('');
  const {refreshRuns, removeRun} = useOutletContext();
  const navigate = useNavigate();

  useEffect(() => {
    if (run && !TERMINAL_STATUSES.has(run.status)) navigate(`/research/${runId}/progress`, {replace: true});
  }, [run, runId, navigate]);

  async function retryGuide() {
    if (retrying || !confirm('Retry guide generation using the saved films and evidence?')) return;
    setRetrying(true);
    setActionError('');
    try {
      await api.retryGuide(runId);
      await refreshRuns();
      navigate(`/research/${runId}/progress`);
    } catch (problem) { setActionError(problem.message); setRetrying(false); }
  }

  if (unavailable) return <main className="page-frame state-page"><span className="eyebrow">Result unavailable</span><h1>We could not open this guide.</h1><p>{error}</p><Link className="primary-button" to="/research/new">Start new research</Link></main>;
  if (!run || !TERMINAL_STATUSES.has(run.status)) return <main className="page-frame state-page"><span className="eyebrow">Opening research</span><h1>Loading the viewing guide…</h1>{error && <p className="form-error">{error}</p>}</main>;
  return <main className="result-page page-frame">
    <div className="result-actions"><Link to="/research/new">New research</Link><button type="button" onClick={() => removeRun({run_id: runId, topic: run.constraints.topic, status: run.status})}>Delete research</button></div>
    {actionError && <p className="form-error" role="alert">{actionError}</p>}
    <Results run={run} onRetryGuide={retryGuide} retryingGuide={retrying} />
  </main>;
}
