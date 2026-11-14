import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

const examples = [
  ['Norse mythology', 'Find five films inspired by Norse mythology and show how they relate to the mythological timeline.'],
  ['State surveillance', 'Find three European films about state surveillance released between 1980 and 2020.'],
  ['Romanian political memory', 'Find three films about Romanian political memory and show their historical context.'],
];
const axisNames = {release: 'Release date', story: 'Story period', myth: 'Mythology', history: 'Historical context'};
const stageNames = {
  queued: 'Queued', planning: 'Planning', searching: 'Finding sources',
  assessing: 'Evaluating films', follow_up: 'Checking evidence', generating: 'Writing the guide',
  validating: 'Validating results', finished: 'Finished',
};
const terminal = new Set(['completed', 'partial', 'failed', 'interrupted']);
const statusNames = {queued: 'Queued', running: 'Running', completed: 'Completed', partial: 'Partial results', failed: 'Failed', interrupted: 'Interrupted'};
const decisionNames = {accepted: 'Accepted', rejected: 'Excluded', needs_evidence: 'Insufficient evidence'};

async function request(path, options = {}) {
  let response;
  try {
    const timeout = path === '/requests/parse' ? 200000 : 15000;
    response = await fetch('/api' + path, {...options, signal: AbortSignal.timeout(timeout)});
  } catch (problem) {
    throw new Error(problem.name === 'TimeoutError' ? 'The request timed out. Try again or check the run later.' : 'Cannot reach the server. Check that it is running.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || data === null) {
    const detail = typeof data?.detail === 'string' ? data.detail : 'The request failed. Check your input or try again later.';
    const problem = new Error(detail);
    problem.status = response.status;
    throw problem;
  }
  return data;
}

function post(path, body) {
  return request(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
}

function sourceLink(url) {
  try {
    const parsed = new URL(url);
    return ['https:', 'http:'].includes(parsed.protocol) ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

function Sources({ids, evidence}) {
  const items = [...new Set(ids)].map(id => evidence.get(id)).filter(Boolean);
  if (!items.length) return null;
  return <details className="sources">
    <summary>View sources <span>{items.length}</span></summary>
    {items.map(item => <div className="source" key={item.id}>
      <a href={sourceLink(item.source_url)} target="_blank" rel="noreferrer">{item.source_title} ↗</a>
      <small>{item.kind === 'web_excerpt' ? 'Web excerpt' : item.kind === 'search_summary' ? 'Search summary' : item.kind === 'metadata' ? 'Metadata' : 'Source summary'}</small>
      {item.kind !== 'metadata' && <p>{item.body}</p>}
    </div>)}
  </details>;
}

function Claims({claims, evidence}) {
  return claims?.map((claim, index) => <article className="claim" key={index}>
    <h4>{claim.title}</h4><p>{claim.text}</p>
    <Sources ids={claim.evidence_ids} evidence={evidence} />
  </article>);
}

function TimelineFilm({film, onOpen, selected}) {
  const [imageFailed, setImageFailed] = useState(false);
  const path = film.poster_path || '';
  const hasPoster = /^\/[A-Za-z0-9_-]+\.(jpg|png|webp)$/.test(path) && !imageFailed;
  return <button type="button" className="timeline-film" aria-pressed={selected} onClick={() => onOpen(film.id)}>
    {hasPoster
      ? <img className="timeline-poster" src={`https://image.tmdb.org/t/p/w342${path}`}
             alt="" loading="lazy" decoding="async" onError={() => setImageFailed(true)} />
      : <span className="timeline-poster poster-placeholder" aria-hidden="true">No poster</span>}
    <span className="timeline-film-title">{film.title} <span aria-hidden="true">↗</span></span>
  </button>;
}

function FilmDetail({run, filmId, storyEvents, onClose}) {
  const [detail, setDetail] = useState(null);
  const [ratings, setRatings] = useState(null);
  const [error, setError] = useState('');
  const [ratingError, setRatingError] = useState('');
  useEffect(() => {
    let active = true;
    let timer;
    request(`/runs/${run.run_id}/films/${filmId}`).then(data => {
      if (active) setDetail(data);
    }).catch(problem => { if (active) setError(problem.message); });
    async function readRatings(attempt = 0) {
      try {
        const data = await request(`/films/${filmId}/ratings`);
        if (active) { setRatings(data.ratings); setRatingError(''); }
      } catch {
        if (active) { setRatings([]); setRatingError('Ratings could not load. Reopen the film details to retry.'); }
      }
      if (active && run.model_mode !== 'mock' && attempt < 4) {
        timer = setTimeout(() => readRatings(attempt + 1), 2500);
      }
    }
    readRatings();
    return () => { active = false; clearTimeout(timer); };
  }, [run.run_id, run.model_mode, filmId]);

  return <section className="film-detail" aria-label="Film details">
    <div className="section-head"><span className="kicker">Film details</span>
      <button className="text-button" onClick={onClose}>Close</button></div>
    {error && <p role="alert">{error}</p>}
    {!detail && !error && <p>Loading film details…</p>}
    {detail && <>
      <h3>{detail.film.title}</h3>
      <p className="muted">{detail.film.director} · {detail.film.countries.join(' / ')} · {detail.film.year}</p>
      {storyEvents.length > 0 && <p className="muted">
        Story period · {[...new Set(storyEvents.map(event => event.period))].join(' / ')}
      </p>}
      {detail.film.overview && <p>{detail.film.overview}</p>}
      <div className="rating-strip" aria-label="Ratings">
        {ratings?.map(rating => <a className="rating" key={rating.origin + rating.metric}
            href={sourceLink(rating.source_url)} target="_blank" rel="noreferrer">
          <strong>{rating.value}{rating.metric === 'tomatometer' ? '%' : `/${rating.scale}`}</strong>
          <span>{rating.origin} ↗</span>
          {rating.provider === 'demo_snapshot' && <small>Demo rating snapshot</small>}
          <small>{rating.metric === 'audience' ? 'Audience score' : 'Critic score'}
            {rating.count ? ` · ${rating.count} votes` : ''}{rating.as_of ? ` · ${rating.as_of.slice(0, 10)}` : ''}</small>
        </a>)}
        {ratingError && <p role="alert">{ratingError}</p>}
        {ratings?.length === 0 && !ratingError && <p className="muted">No ratings available</p>}
        {ratings === null && <p className="muted">Loading ratings…</p>}
      </div>
      <details className="release-details"><summary>Release records and story setting</summary>
        {detail.film.releases.map((release, i) => <p key={i}>
          <a href={sourceLink(release.source_url)} target="_blank" rel="noreferrer">{release.label} ↗</a>
          {' '}{release.region} {({premiere: 'Premiere', theatrical: 'Theatrical release', release_year: 'Release year', tmdb_release: 'TMDB release date'})[release.kind]}
        </p>)}
        {detail.film.story && <p>Story or archival setting · {detail.film.story}</p>}
      </details>
      {detail.guide ? <Claims claims={[detail.guide.reason, ...detail.guide.primer, ...detail.guide.background]}
                              evidence={new Map(detail.evidence.map(item => [item.id, item]))} />
        : <Sources ids={detail.evidence.filter(item => item.film_id === filmId).map(item => item.id)}
                   evidence={new Map(detail.evidence.map(item => [item.id, item]))} />}
    </>}
  </section>;
}

// Older results may have a period label but no numeric year.
function storyYear(event) {
  const label = event.period.trim();
  const century = label.match(/^(?:公元)?(\d{1,2})\s*世纪(?:\s*(初|早期|中叶|中期|末|晚期))?$/);
  if (century) {
    const offset = {初: 0, 早期: 0, 中叶: 40, 中期: 40, 末: 70, 晚期: 70};
    return (Number(century[1]) - 1) * 100 + (offset[century[2]] ?? 0) + 1;
  }
  if (Number.isInteger(event.sort_year)) return event.sort_year;
  const year = label.match(/^(?:公元)?(\d{3,4})(?=\s*(?:年|年代|$|[–—-]))/);
  if (year) return Number(year[1]);
  const englishCentury = label.match(/^(?:(early|mid|late)[ -])?(\d{1,2})(?:st|nd|rd|th)[ -]century$/i);
  if (englishCentury) {
    const offset = {early: 0, mid: 40, late: 70};
    return (Number(englishCentury[2]) - 1) * 100 + (offset[englishCentury[1]?.toLowerCase()] ?? 0) + 1;
  }
  const englishYear = label.match(/^(\d{3,4})(?=s\b|[–—-]|\s|$)/);
  return englishYear ? Number(englishYear[1]) : null;
}

function Results({run, onRetryGuide, retryingGuide}) {
  const [axis, setAxis] = useState('viewing');
  const [zoom, setZoom] = useState(100);
  const [filmId, setFilmId] = useState(null);
  const timelineViewport = useRef(null);
  const detailPanel = useRef(null);
  const study = run.study || {};
  const films = new Map((study.films || []).map(film => [film.id, film]));
  const evidence = new Map((study.evidence || []).map(item => [item.id, item]));
  const views = study.timeline_views || {};
  const guide = run.guide;
  const order = (guide?.viewing_order ?? [...films.keys()]).map(id => films.get(id)).filter(Boolean);
  const reasons = new Map((guide?.film_guides || []).map(item => [item.film_id, item.reason]));
  const decisions = new Map((study.decisions || []).map(item => [item.film_id, item.reason]));
  const datedFilms = order.map(film => {
    const events = (views.story || []).filter(event => event.film_ids.includes(film.id));
    const dated = events.map(event => ({event, year: storyYear(event)}))
      .filter(item => item.year !== null).sort((a, b) => a.year - b.year);
    return {film, events, year: dated[0]?.year ?? null, period: dated[0]?.event.period || events[0]?.period};
  });
  const chronological = datedFilms.filter(item => item.year !== null).sort((a, b) => a.year - b.year);
  const undated = datedFilms.filter(item => item.year === null);
  const nodes = {
    viewing: order.map((film, index) => ({film, period: `Film ${index + 1}`})),
    story: chronological,
    release: [...order].sort((a, b) => a.year - b.year || (a.releases?.[0]?.label ?? '').localeCompare(b.releases?.[0]?.label ?? ''))
      .map(film => ({film, period: film.releases?.[0]?.label || film.year})),
  };
  const tabs = {viewing: guide ? 'Viewing order' : 'Selection order', story: 'Story period', release: 'Release date'};
  const selected = datedFilms.find(item => item.film.id === filmId);

  function changeAxis(next) {
    setAxis(next);
    if (timelineViewport.current) timelineViewport.current.scrollLeft = 0;
  }

  function openFilm(id) {
    setFilmId(id);
    requestAnimationFrame(() => {
      detailPanel.current?.focus({preventScroll: true});
      detailPanel.current?.scrollIntoView({block: 'nearest'});
    });
  }

  return <div className="results">
    <div className="section-head"><span className="kicker">Viewing guide</span><span className="pill">{statusNames[run.status]}</span></div>
    <h2>{guide?.title || run.constraints.topic}</h2>
    {run.model_mode === 'mock' && <p className="notice">This guide was created in demo mode using local sources.</p>}
    {guide?.introduction && <p className="intro">{guide.introduction}</p>}
    {run.error && <p className="notice" role="alert">{run.error}</p>}
    {run.status === 'partial' && run.progress.at(-1) && <p className="notice">{run.progress.at(-1).message}</p>}
    {!guide && (study.films?.length ?? 0) > 0 && ['partial', 'failed', 'interrupted'].includes(run.status) &&
      <button className="button retry-guide" type="button" disabled={retryingGuide} onClick={onRetryGuide}>
        {retryingGuide ? 'Retrying guide…' : 'Retry guide generation'}
      </button>}
    {(study.limitations || []).map(note => <p className="notice" key={note}>{note}</p>)}
    {films.size > 0 && <section className="timeline-section" aria-labelledby="film-timeline-heading">
      <div className="section-head">
        <h3 id="film-timeline-heading">Film timeline</h3>
        <div className="segments" role="group" aria-label="Timeline order">
          {Object.entries(tabs).map(([key, name]) =>
            <button key={key} type="button" aria-pressed={axis === key} onClick={() => changeAxis(key)}>{name}</button>)}
        </div>
      </div>
      <div className="timeline-toolbar">
        <p className="muted">{axis === 'story'
          ? 'Sorted by the start of each period. Some periods may overlap.'
          : 'Scroll sideways. Select a poster for details.'}</p>
        <div className="timeline-zoom" role="group" aria-label="Timeline zoom">
          <button type="button" aria-label="Zoom out" disabled={zoom === 100} onClick={() => setZoom(zoom - 25)}>−</button>
          <output aria-label="Zoom level">{zoom}%</output>
          <button type="button" aria-label="Zoom in" disabled={zoom === 225} onClick={() => setZoom(zoom + 25)}>+</button>
          <button type="button" aria-label="Reset timeline zoom" disabled={zoom === 100} onClick={() => setZoom(100)}>Reset</button>
        </div>
      </div>
      {nodes[axis].length > 0 && <div className="film-track-viewport" ref={timelineViewport} tabIndex={0} aria-label="Scrollable film timeline">
        <ol className="film-track" style={{'--zoom': zoom / 100}}>
          {nodes[axis].map(({film, period, events}) => <li className="film-node" key={film.id}>
            <div className="film-node-period">{period}</div>
            <div className="film-node-content">
              <TimelineFilm film={film} selected={film.id === filmId} onOpen={openFilm} />
              <p>{reasons.get(film.id)?.text || decisions.get(film.id)}</p>
              {axis === 'story' && <Sources ids={events.flatMap(event => event.evidence_ids)} evidence={evidence} />}
            </div>
          </li>)}
        </ol>
      </div>}
      {axis === 'story' && undated.length > 0 && <div className="timeline-other-films">
        <h4>Films with no confirmed story date</h4>
        <div className="timeline-films">
          {undated.map(({film, period}) => <div key={film.id}>
            <TimelineFilm film={film} selected={film.id === filmId} onOpen={openFilm} />
            {period && <small className="muted">{period}</small>}
          </div>)}
        </div>
      </div>}
      {selected && <div className="timeline-details" ref={detailPanel} tabIndex={-1}>
        <FilmDetail key={filmId} run={run} filmId={filmId} storyEvents={selected.events} onClose={() => setFilmId(null)} />
      </div>}
      {['history', 'myth'].map(key => views[key]?.length > 0 && <details className="timeline-reference" key={key}>
        <summary>{axisNames[key]}</summary>
        <ol className="timeline">
          {views[key].map(event => <li key={event.id}>
            <div className="time">{event.period}</div>
            <div className="event">
              <strong>{event.label}</strong>
              {event.version && <small>{event.version}</small>}
              {event.relation && <small>{event.relation}</small>}
              <Sources ids={event.evidence_ids} evidence={evidence} />
              {event.film_ids.filter(id => films.has(id)).map(id =>
                <button type="button" className="text-button timeline-reference-film" key={id}
                        onClick={() => openFilm(id)}>{films.get(id).title} ↗</button>)}
            </div>
          </li>)}
        </ol>
      </details>)}
    </section>}
    {guide?.common_primer?.length > 0 && <section className="primer">
      <h3>Before you watch</h3><Claims claims={guide.common_primer} evidence={evidence} />
    </section>}
    {guide?.discussion_questions.length > 0 && <section className="post-view">
      <h3>Discussion questions</h3>
      <ul>{guide.discussion_questions.map((question, index) => <li key={index}>{question}</li>)}</ul>
    </section>}
    {study.decisions?.length > 0 && <details className="candidate-list"><summary>Candidate decisions</summary>
      {study.decisions.map(item => <article key={item.film_id}>
        <strong>{item.title} · {item.year}</strong><small>{decisionNames[item.status]}</small><p>{item.reason}</p>
        <Sources ids={item.evidence_ids} evidence={evidence} />
      </article>)}
    </details>}
  </div>;
}

function App() {
  const [prompt, setPrompt] = useState('');
  const [constraints, setConstraints] = useState(null);
  const [clarification, setClarification] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [health, setHealth] = useState(null);
  const [runId, setRunId] = useState(new URL(location.href).searchParams.get('run'));
  const [run, setRun] = useState(null);
  const [pollError, setPollError] = useState('');
  const [unavailable, setUnavailable] = useState(false);
  const [recentRuns, setRecentRuns] = useState([]);
  const [pollRevision, setPollRevision] = useState(0);
  const submission = useRef(null);
  const locked = busy || (!!runId && !unavailable && (!run || !terminal.has(run.status)));

  useEffect(() => {
    request('/runs').then(data => setRecentRuns(data.runs)).catch(() => {});
  }, [runId, run?.status]);

  function openRun(id) {
    if (id === runId) return;
    const url = new URL(location.href);
    if (id) url.searchParams.set('run', id);
    else url.searchParams.delete('run');
    history.replaceState({}, '', url);
    setRun(null);
    setRunId(id);
    setPrompt('');
    setConstraints(null);
    setClarification('');
    setError('');
    setPollError('');
    setUnavailable(false);
    submission.current = null;
  }


  useEffect(() => { request('/health').then(setHealth).catch(() => setError('Cannot check the server status. Check the connection.')); }, []);
  useEffect(() => {
    if (!runId) return;
    let active = true;
    let timer;
    let restored = false;
    async function poll(delay = 2000) {
      try {
        const result = await request(`/runs/${runId}`);
        if (!active) return;
        setPollError('');
        setRun(result);
        if (!restored) {
          setPrompt(result.constraints.topic);
          setConstraints(result.constraints);
          restored = true;
        }
        if (!terminal.has(result.status)) timer = setTimeout(poll, 2000);
      } catch (problem) {
        if (!active) return;
        setPollError(problem.message);
        if ([404, 422].includes(problem.status)) {
          setUnavailable(true);
        } else {
          const nextDelay = Math.min(delay * 2, 10000);
          timer = setTimeout(() => poll(nextDelay), nextDelay);
        }
      }
    }
    poll();
    return () => { active = false; clearTimeout(timer); };
  }, [runId, pollRevision]);

  function editPrompt(value) {
    submission.current = null;
    setPrompt(value);
    setConstraints(null);
    setClarification('');
  }

  function editCondition(key, value) {
    submission.current = null;
    setConstraints({...constraints, [key]: value});
  }

  async function parse(event) {
    event.preventDefault();
    if (locked) return;
    setBusy(true);
    setError('');
    try {
      const data = await post('/requests/parse', {prompt});
      submission.current = null;
      setConstraints(data.constraints);
      setClarification(data.clarification);
    } catch (problem) {
      setError(problem.message);
    } finally {
      setBusy(false);
    }
  }

  async function start(event) {
    event.preventDefault();
    if (locked) return;
    setBusy(true);
    setError('');
    try {
      if (constraints.release_from && constraints.release_to && constraints.release_from > constraints.release_to) {
        throw new Error('The start year must be no later than the end year.');
      }
      submission.current ??= crypto.randomUUID();
      const result = await post('/runs', {submission_key: submission.current, constraints});
      submission.current = null;
      const url = new URL(location.href);
      url.searchParams.set('run', result.run_id);
      history.replaceState({}, '', url);
      setPollError('');
      setUnavailable(false);
      setRun(null);
      setRunId(result.run_id);
    } catch (problem) {
      setError(problem.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteRun(item) {
    if (busy || !confirm(`Delete “${item.topic}”? This cannot be undone.`)) return;
    setBusy(true);
    setError('');
    try {
      await request(`/runs/${item.run_id}`, {method: 'DELETE'});
      setRecentRuns(items => items.filter(runItem => runItem.run_id !== item.run_id));
      if (item.run_id === runId) openRun(null);
    } catch (problem) {
      setError(problem.message);
    } finally {
      setBusy(false);
    }
  }

  async function retryGuide() {
    if (!runId || busy || !confirm('Retry guide generation using the saved films and evidence?')) return;
    setBusy(true);
    setError('');
    try {
      await post(`/runs/${runId}/retry-guide`, {});
      setPollError('');
      setUnavailable(false);
      setRun(null);
      setPollRevision(value => value + 1);
    } catch (problem) {
      setError(problem.message);
    } finally {
      setBusy(false);
    }
  }

  const finished = run && terminal.has(run.status);
  return <>
    <header className="masthead"><a className="brand" href="/">CineContext</a>
      <span className="tagline">Film research</span>
      {health && <span className="pill mode">{health.model_mode === 'mock' ? 'Local demo'
        : health.model_mode === 'deepseek' ? `DeepSeek · ${health.model_id}`
        : health.model_mode === 'anyrouter' ? `AnyRouter · ${health.model_id}` : 'TACC Qwen'}</span>}
    </header>
    <main className="layout">
      <aside className="sidebar">
        {health && (!health.metadata_enabled || !health.web_enabled) && <p className="notice">
          {health.metadata_enabled ? 'TMDB metadata is enabled. ' : 'External film metadata is unavailable. '}
          {!health.web_enabled && 'Web search is unavailable.'}
        </p>}
        <form onSubmit={parse}>
          <label className="kicker" htmlFor="prompt">Research question</label>
          <textarea id="prompt" value={prompt} onChange={event => editPrompt(event.target.value)}
                    placeholder="What would you like to explore through film?"
                    rows="4" required maxLength="500" disabled={locked} />
          {!prompt.trim() && !locked && <details className="example-prompts">
            <summary>Example questions</summary>
            <div className="examples">{examples.map(([name, value]) =>
              <button type="button" key={name} onClick={() => editPrompt(value)}>{name}</button>)}</div>
          </details>}
          <button className="button secondary" type="submit" disabled={locked || !prompt.trim()}>{busy && !constraints ? 'Analyzing…' : 'Analyze request'}</button>
        </form>
        {constraints && <form className="conditions" onSubmit={start}>
          <fieldset disabled={locked}>
          <div className="condition-summary">
            <h2>{constraints.theme || 'Film criteria'}</h2>
            <p className="muted">{constraints.count} {constraints.count === 1 ? 'film' : 'films'} · {axisNames[constraints.axis]}</p>
            {clarification && <p className="muted">{clarification}</p>}
          </div>
          <details className="condition-options">
          <summary>Edit criteria</summary>
          <label>Theme <input value={constraints.theme} maxLength="100" onChange={event => editCondition('theme', event.target.value)} /></label>
          <div className="fields">
            <label>Film count <input type="number" min="1" max="5" value={constraints.count}
                onChange={event => editCondition('count', event.target.valueAsNumber || 1)} /></label>
            <label>Production region <input value={constraints.region} placeholder="Any"
                onChange={event => editCondition('region', event.target.value)} /></label>
            <label>Released from <input type="number" min="1880" max="2200" value={constraints.release_from ?? ''}
                onChange={event => editCondition('release_from', event.target.valueAsNumber || null)} /></label>
            <label>Released through <input type="number" min="1880" max="2200" value={constraints.release_to ?? ''}
                onChange={event => editCondition('release_to', event.target.valueAsNumber || null)} /></label>
          </div>
          <label>Story setting <input value={constraints.story_period} placeholder="Any"
               onChange={event => editCondition('story_period', event.target.value)} /></label>
          <label>Film movement <input value={constraints.movement} placeholder="Any"
               onChange={event => editCondition('movement', event.target.value)} /></label>
          <label>Timeline <select value={constraints.axis} onChange={event => editCondition('axis', event.target.value)}>
            {Object.entries(axisNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
          </select></label>
          </details>
          <button className="button" disabled={locked} type="submit">{locked ? 'Working…' : run ? 'Run again' : 'Start research'} <span aria-hidden="true">→</span></button>
          </fieldset>
        </form>}
        {error && <p className="notice" role="alert">{error}</p>}
        {pollError && <p className="notice" role="alert">{pollError}{!unavailable && ' Reconnecting automatically.'}</p>}
        {runId && <button type="button" className="text-button" disabled={busy} onClick={() => openRun(null)}>New research</button>}
        {recentRuns.length > 0 && <details className="progress-panel">
          <summary>Recent research</summary>
          <ul className="recent-runs">{recentRuns.map(item => <li key={item.run_id}>
            <div className="recent-run-row">
              <button className="text-button" disabled={busy} onClick={() => openRun(item.run_id)}>{item.topic}</button>
              <button className="delete-run" type="button" disabled={busy || !terminal.has(item.status)}
                      aria-label={`Delete ${item.topic}`} title={terminal.has(item.status) ? 'Delete research' : 'Active research cannot be deleted'}
                      onClick={() => deleteRun(item)}>Delete</button>
            </div>
            <small className="muted">{statusNames[item.status]}</small>
          </li>)}</ul>
        </details>}
        {run && <details className="progress-panel">
          <summary>Research progress · {finished ? statusNames[run.status] : stageNames[run.stage]}</summary>
          <ol>{run.progress.map((item, index) => <li key={index}><strong>{stageNames[item.stage]}</strong><p>{item.message}</p></li>)}</ol>
        </details>}
      </aside>
      <section className="workspace" aria-label="Research results">
        {finished ? <Results key={run.run_id} run={run} onRetryGuide={retryGuide} retryingGuide={busy} />
          : runId ? <div className="empty" aria-live="polite">
            <span className="kicker">Research progress</span><h1>{unavailable ? 'Unable to open this run' : run ? stageNames[run.stage] : 'Loading run'}</h1>
            <p>{unavailable ? 'Open a recent run or start a new one.' : run?.progress.at(-1)?.message || 'Progress updates automatically while the run is active.'}</p>
          </div> : <div className="empty">
            <span className="kicker">Film, history, and myth</span><h1>Find films through a theme</h1>
            <p>Find films on a topic, check the sources, and explore them on a timeline.</p>
            {health?.model_mode === 'mock' && <p className="muted">Demo topics include Norse mythology, state surveillance, and Romanian political memory.</p>}
          </div>}
      </section>
    </main>
    {health?.metadata_enabled && <footer className="tmdb-credit" aria-label="Credits">
      <a href="https://www.themoviedb.org/" target="_blank" rel="noreferrer">
        <img src="https://www.themoviedb.org/assets/v4/logos/v2/blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg"
             alt="TMDB" width="76" height="10" />
      </a>
      <span>This product uses the TMDB API but is not endorsed or certified by TMDB.</span>
    </footer>}
  </>;
}

createRoot(document.getElementById('root')).render(<App />);
