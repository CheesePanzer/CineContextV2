import React, {useState} from 'react';
import {Navigate, useNavigate, useOutletContext} from 'react-router-dom';
import {api} from '../api/client.js';
import {AXIS_NAMES} from '../app/constants.js';
import {clearDraft, loadDraft, saveDraft} from '../state/researchDraft.js';

export default function CriteriaPage() {
  const [draft] = useState(() => loadDraft());
  const [constraints, setConstraints] = useState(draft.constraints);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const {health, refreshRuns} = useOutletContext();
  if (!draft.prompt || !constraints) return <Navigate to="/research/new" replace />;

  function edit(key, value) {
    const next = {...constraints, [key]: value};
    setConstraints(next);
    saveDraft({...draft, constraints: next});
  }

  async function confirmCriteria(event) {
    event.preventDefault();
    if (constraints.release_from && constraints.release_to && constraints.release_from > constraints.release_to) {
      setError('The start year must be no later than the end year.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = await api.createRun(crypto.randomUUID(), constraints);
      if (!result?.run_id) throw new Error('The server created the research without returning a run ID.');
      navigate(`/research/${result.run_id}/progress`, {replace: true});
      clearDraft();
      refreshRuns();
    } catch (problem) { setError(problem.message); }
    finally { setBusy(false); }
  }

  return <main className="criteria-page page-frame">
    <header className="page-heading"><span className="eyebrow">Review the brief</span><h1>Research criteria</h1>
      <p>Adjust the interpretation before the archive begins its research.</p></header>
    <form onSubmit={confirmCriteria}>
      <section className="criteria-summary">
        <span>Original question</span><blockquote>{draft.prompt}</blockquote>
        {draft.clarification && <p>{draft.clarification}</p>}
      </section>
      <section className="criteria-card">
        <div className="field wide"><label htmlFor="theme">Theme</label><input id="theme" value={constraints.theme} maxLength="100" required onChange={e => edit('theme', e.target.value)} /></div>
        <div className="field"><label htmlFor="count">Film count</label><input id="count" type="number" min="1" max="5" value={constraints.count} onChange={e => edit('count', e.target.valueAsNumber || 1)} /></div>
        <div className="field"><label htmlFor="region">Production region</label><input id="region" value={constraints.region} placeholder="Any region" onChange={e => edit('region', e.target.value)} /></div>
        <div className="field"><label htmlFor="release-from">Released from</label><input id="release-from" type="number" min="1880" max="2200" value={constraints.release_from ?? ''} placeholder="Any year" onChange={e => edit('release_from', e.target.valueAsNumber || null)} /></div>
        <div className="field"><label htmlFor="release-to">Released through</label><input id="release-to" type="number" min="1880" max="2200" value={constraints.release_to ?? ''} placeholder="Any year" onChange={e => edit('release_to', e.target.valueAsNumber || null)} /></div>
        <div className="field"><label htmlFor="story">Story setting</label><input id="story" value={constraints.story_period} placeholder="Any period" onChange={e => edit('story_period', e.target.value)} /></div>
        <div className="field"><label htmlFor="movement">Film movement</label><input id="movement" value={constraints.movement} placeholder="Any movement" onChange={e => edit('movement', e.target.value)} /></div>
        <div className="field wide"><label htmlFor="axis">Primary timeline</label><select id="axis" value={constraints.axis} onChange={e => edit('axis', e.target.value)}>{Object.entries(AXIS_NAMES).map(([key, value]) => <option value={key} key={key}>{value}</option>)}</select></div>
      </section>
      <section className="capability-grid" aria-label="Research capabilities">
        <article><i className={health?.web_enabled ? 'available' : ''} /><div><strong>Source research</strong><p>{health?.web_enabled ? 'Web research is available.' : 'Limited to configured local sources.'}</p></div></article>
        <article><i className={health?.metadata_enabled ? 'available' : ''} /><div><strong>Film metadata</strong><p>{health?.metadata_enabled ? 'Posters and release data enabled.' : 'External metadata unavailable.'}</p></div></article>
        <article><i className={health?.ratings_enabled ? 'available' : ''} /><div><strong>Ratings</strong><p>{health?.ratings_enabled ? 'External rating snapshots enabled.' : 'Ratings may be unavailable.'}</p></div></article>
      </section>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions"><button type="button" className="quiet-button" disabled={busy} onClick={() => navigate('/research/new')}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? 'Starting research…' : 'Confirm and research'} <b aria-hidden="true">→</b></button></div>
    </form>
  </main>;
}
