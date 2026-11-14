import React, {useEffect} from 'react';
import {Link, useNavigate, useOutletContext, useParams} from 'react-router-dom';
import {STAGE_NAMES} from '../app/constants.js';
import {TERMINAL_STATUSES, useRunPolling} from '../hooks/useRunPolling.js';

const STEPS = [
  {name: 'Planning', stages: ['queued', 'planning']},
  {name: 'Research', stages: ['searching']},
  {name: 'Evaluation', stages: ['assessing', 'follow_up']},
  {name: 'Guide', stages: ['generating']},
  {name: 'Validation', stages: ['validating']},
  {name: 'Complete', stages: ['finished']},
];

export default function ProgressPage() {
  const {runId} = useParams();
  const {run, error, unavailable} = useRunPolling(runId);
  const {refreshRuns} = useOutletContext();
  const navigate = useNavigate();
  const currentIndex = Math.max(0, STEPS.findIndex(step => step.stages.includes(run?.stage)));

  useEffect(() => {
    if (run && TERMINAL_STATUSES.has(run.status)) {
      refreshRuns();
      navigate(`/research/${runId}`, {replace: true});
    }
  }, [run?.status, runId]);

  if (unavailable) return <main className="page-frame state-page"><span className="eyebrow">Research unavailable</span><h1>We could not open this run.</h1><p>{error}</p><Link className="primary-button" to="/research/new">Start new research</Link></main>;
  return <main className="progress-page page-frame" aria-live="polite">
    <header className="page-heading"><span className="eyebrow">Research in progress</span><h1>{run?.constraints.topic || 'Opening the archive…'}</h1><p>Your viewing guide updates automatically as the research moves through each stage.</p></header>
    <ol className="coarse-stepper" aria-label="Research progress">
      {STEPS.map((step, index) => <li className={index < currentIndex ? 'complete' : index === currentIndex ? 'current' : ''} key={step.name} aria-current={index === currentIndex ? 'step' : undefined}><span>{index < currentIndex ? '✓' : index + 1}</span><strong>{step.name}</strong></li>)}
    </ol>
    <section className="progress-focus">
      <div className="stage-mark"><span /><span /><span /></div>
      <div><span className="eyebrow">Current stage</span><h2>{run ? STAGE_NAMES[run.stage] || run.stage : 'Loading run'}</h2><p>{run?.progress.at(-1)?.message || 'Waiting for the first progress update.'}</p></div>
    </section>
    {error && <p className="form-error" role="alert">{error} Reconnecting automatically.</p>}
    <section className="activity-log"><div className="section-head"><h2>Research activity</h2><span>Live</span></div>
      {run?.progress.length ? <ol>{[...run.progress].reverse().map((item, index) => <li key={`${item.stage}-${run.progress.length - index}`}><i /><div><strong>{STAGE_NAMES[item.stage] || item.stage}</strong><p>{item.message}</p></div></li>)}</ol> : <p className="muted">Activity will appear here as it is reported.</p>}
    </section>
  </main>;
}
