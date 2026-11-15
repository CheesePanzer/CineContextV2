import React, {useEffect, useRef, useState} from 'react';
import {Link, useNavigate, useOutletContext, useParams} from 'react-router-dom';
import {STAGE_NAMES} from '../app/constants.js';
import {getResearchTitle} from '../app/researchTitle.js';
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
  const progress = run?.progress ?? [];
  return <main className="progress-page page-frame">
    <header className="page-heading">
      <span className="eyebrow">Research in progress</span>
      <h1>{run ? getResearchTitle(run.constraints) : 'Opening research'}</h1>
      {run?.constraints.topic && <details className="original-question" key={runId}>
        <summary>Original question</summary>
        <p>{run.constraints.topic}</p>
      </details>}
    </header>
    <ol className="coarse-stepper" aria-label="Research progress">
      {STEPS.map((step, index) => <li className={index < currentIndex ? 'complete' : index === currentIndex ? 'current' : ''}
          key={step.name} aria-current={index === currentIndex ? 'step' : undefined}>
        <span aria-hidden="true">{index < currentIndex ? '✓' : index + 1}</span><strong>{step.name}</strong>
      </li>)}
    </ol>
    <section className="progress-focus" aria-live="polite">
      <div className="stage-mark" aria-hidden="true"><span /><span /><span /></div>
      <div>
        <span className="eyebrow">Current stage</span>
        <h2>{run ? STAGE_NAMES[run.stage] || run.stage : 'Loading run'}</h2>
        <p>{progress.at(-1)?.message || 'Waiting for the first progress update.'}</p>
      </div>
    </section>
    {error && <p className="form-error" role="alert">{error} Reconnecting automatically.</p>}
    <ResearchActivity key={runId} progress={progress} />
  </main>;
}

function ResearchActivity({progress}) {
  const scrollArea = useRef(null);
  const entries = useRef(null);
  const [scrollable, setScrollable] = useState(false);
  const hasEntries = progress.length > 0;

  useEffect(() => {
    const area = scrollArea.current;
    if (!area) return;
    function measure() {
      setScrollable(area.scrollHeight > area.clientHeight);
    }
    const observer = new ResizeObserver(measure);
    measure();
    observer.observe(area);
    observer.observe(entries.current);
    return () => observer.disconnect();
  }, [hasEntries]);

  return <section className="activity-log" aria-labelledby="activity-title">
    <div className="section-head">
      <h2 id="activity-title">Research activity</h2>
      <span>Live</span>
    </div>
    {hasEntries ? <>
      <div className="activity-log-scroll" role="region" aria-label="Research activity history"
          aria-describedby={scrollable ? 'activity-scroll-hint' : undefined} tabIndex={scrollable ? 0 : undefined} ref={scrollArea}>
        <ol ref={entries}>{[...progress].reverse().map((item, index) => <li key={progress.length - index}>
          <strong>{STAGE_NAMES[item.stage] || item.stage}</strong>
          <p>{item.message}</p>
        </li>)}</ol>
      </div>
      {scrollable && <p className="activity-scroll-hint" id="activity-scroll-hint">Scroll to view earlier updates</p>}
    </> : <p className="muted">Activity will appear here as it is reported.</p>}
  </section>;
}
