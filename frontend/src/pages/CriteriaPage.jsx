import React, {useState} from 'react';
import {Navigate, useNavigate, useOutletContext} from 'react-router-dom';
import {api} from '../api/client.js';
import {AXIS_NAMES, TACC_STATUS_NAMES, TACC_WAITING_STATES} from '../app/constants.js';
import {clearDraft, loadDraft, saveDraft} from '../state/researchDraft.js';

const GENRES = ['Historical drama', 'Adventure', 'War', 'Mystery', 'Documentary', 'Fantasy'];

function PreferenceChoices({label, options, values, onChange}) {
  const [custom, setCustom] = useState('');
  const choices = [...new Set([...options, ...values])];
  const newValue = custom.trim();

  function addPreference() {
    if (!newValue || values.length >= 5 || values.includes(newValue)) return;
    onChange([...values, newValue]);
    setCustom('');
  }

  return <fieldset className="preference-choices">
    <legend>{label}</legend>
    <div>{choices.map(option => <label key={option}>
      <input type="checkbox" checked={values.includes(option)}
        disabled={!values.includes(option) && values.length >= 5}
        onChange={event => onChange(event.target.checked ? [...values, option] : values.filter(value => value !== option))} />
      {option}
    </label>)}</div>
    <div className="custom-preference">
      <input aria-label={`Custom ${label.toLowerCase()}`} value={custom} maxLength="80"
        placeholder="Add your own" onChange={event => setCustom(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'Enter') {
            event.preventDefault();
            addPreference();
          }
        }} />
      <button type="button" className="quiet-button" disabled={!newValue || values.length >= 5 || values.includes(newValue)}
        onClick={addPreference}>Add</button>
    </div>
  </fieldset>;
}

export default function CriteriaPage() {
  const [draft] = useState(() => loadDraft());
  const [constraints, setConstraints] = useState(draft.constraints);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const {health, refreshRuns} = useOutletContext();
  const waitingForModel = TACC_WAITING_STATES.has(health?.model_status);
  const match = draft.referenceMatch;
  const themes = match?.suggested_themes?.length ? match.suggested_themes : (draft.suggestedThemes ?? []);
  const ratingsNote = [
    health?.tmdb_ratings_enabled && 'TMDB audience scores',
    health?.omdb_ratings_enabled && 'OMDb ratings',
  ].filter(Boolean).join(' and ');
  if (!draft.prompt || !constraints) return <Navigate to="/research/new" replace />;
  const hasStorySetting = Boolean(constraints.story_period?.trim());

  function edit(key, value) {
    const next = {...constraints, [key]: value};
    setConstraints(next);
    saveDraft({...draft, constraints: next});
  }

  async function confirmCriteria(event) {
    event.preventDefault();
    if (busy || waitingForModel) return;
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
      <p>Review the fields below. Your confirmed criteria guide the film research.</p></header>
    <form onSubmit={confirmCriteria}>
      <section className="criteria-summary">
        <span>Original question</span><blockquote>{draft.prompt}</blockquote>
        <p><strong>Initial interpretation</strong><br />
          {draft.preliminaryAnalysis || 'Review the subject, setting, and style to decide what should make the films feel similar.'}
        </p>
        {match ? <div className="reference-match">
          <p><strong>Reference work’s setting</strong><br />{match.title} · {match.story_setting}<br />
            {match.context && <>{match.context}<br /></>}
            <a href={match.source_url} target="_blank" rel="noreferrer">{match.source_title}</a>
          </p>
        </div> : draft.clarification && <p>{draft.clarification}</p>}
      </section>
      <section className="criteria-card">
        <div className="field wide"><label htmlFor="theme">Main theme</label><input id="theme" value={constraints.theme} maxLength="100" required onChange={e => edit('theme', e.target.value)} />
          {!!themes.length && <div className="criteria-suggestions">{themes.map(theme =>
            <button className="quiet-button" type="button" key={theme} aria-pressed={constraints.theme === theme}
              onClick={() => edit('theme', theme)}>{theme}</button>
          )}</div>}
        </div>
        <div className="wide">
          <PreferenceChoices label="Preferred genres" options={[...GENRES, ...(draft.constraints.preferred_genres ?? [])]}
            values={constraints.preferred_genres ?? []} onChange={value => edit('preferred_genres', value)} />
        </div>
        <div className="wide">
          <PreferenceChoices label="Preferred styles" options={[...(draft.suggestedStyles ?? []), ...(draft.constraints.preferred_styles ?? []),
            'Grounded realism', 'Slow-paced', 'Action-driven', 'Character-driven']}
            values={constraints.preferred_styles ?? []} onChange={value => edit('preferred_styles', value)} />
        </div>
        <div className="field"><label htmlFor="count">Film count</label><input id="count" type="number" min="1" max="5" value={constraints.count} onChange={e => edit('count', e.target.valueAsNumber || 1)} /></div>
        <div className="field"><label htmlFor="region">Production region</label><input id="region" value={constraints.region} placeholder="Any region" onChange={e => edit('region', e.target.value)} /></div>
        <div className="field"><label htmlFor="release-from">Released from</label><input id="release-from" type="number" min="1880" max="2200" value={constraints.release_from ?? ''} placeholder="Any year" onChange={e => edit('release_from', e.target.valueAsNumber || null)} /></div>
        <div className="field"><label htmlFor="release-to">Released through</label><input id="release-to" type="number" min="1880" max="2200" value={constraints.release_to ?? ''} placeholder="Any year" onChange={e => edit('release_to', e.target.valueAsNumber || null)} /></div>
        <div className="field"><label htmlFor="story">Story setting</label><input id="story" value={constraints.story_period} maxLength="120" placeholder="e.g. Bohemia in the early 1400s" onChange={e => edit('story_period', e.target.value)} /></div>
        <div className="field">
          <span className="field-label">Setting match</span>
          <label className="setting-switch">
            <input type="checkbox" role="switch" disabled={!hasStorySetting}
              aria-describedby="setting-match-hint"
              checked={constraints.setting_match === 'nearby'}
              onChange={event => edit('setting_match', event.target.checked ? 'nearby' : 'strict')} />
            <span>Allow nearby places or periods</span>
          </label>
          <p className="field-hint" id="setting-match-hint">
            {hasStorySetting ? 'Turn off to require the specified story setting.' : 'This option becomes available when a story setting is provided.'}
          </p>
        </div>
        <div className="field"><label htmlFor="movement">Film movement</label><input id="movement" value={constraints.movement} placeholder="Any movement" onChange={e => edit('movement', e.target.value)} /></div>
        <div className="field"><label htmlFor="avoid">Exclude from results</label><input id="avoid" maxLength="180"
          value={constraints.avoid ?? ''} placeholder="e.g. supernatural plots" onChange={e => edit('avoid', e.target.value)} /></div>
        <div className="field wide"><label htmlFor="axis">Primary timeline</label><select id="axis" value={constraints.axis} onChange={e => edit('axis', e.target.value)}>{Object.entries(AXIS_NAMES).map(([key, value]) => <option value={key} key={key}>{value}</option>)}</select></div>
      </section>
      <section className="capability-grid" aria-label="Research capabilities">
        <article><i className={health?.web_enabled ? 'available' : ''} /><div><strong>Source research</strong><p>{health?.web_enabled ? 'Web research is available.' : 'Limited to configured local sources.'}</p></div></article>
        <article><i className={health?.metadata_enabled ? 'available' : ''} /><div><strong>Film metadata</strong><p>{health?.metadata_enabled ? 'Posters and release data enabled.' : 'External metadata unavailable.'}</p></div></article>
        <article><i className={health?.ratings_enabled ? 'available' : ''} /><div><strong>Ratings</strong>
          <p>{ratingsNote ? `${ratingsNote} appear in film details when available.` : 'No live rating provider is configured.'}</p>
        </div></article>
      </section>
      {error && <p className="form-error" role="alert">{error}</p>}
      {waitingForModel && <p className="capability-note" role="status">{TACC_STATUS_NAMES[health.model_status]}.</p>}
      <div className="form-actions"><button type="button" className="quiet-button" disabled={busy} onClick={() => navigate('/research/new')}>Cancel</button><button className="primary-button" disabled={busy || waitingForModel}>{busy ? 'Starting research…' : 'Confirm and research'} <b aria-hidden="true">→</b></button></div>
    </form>
  </main>;
}
