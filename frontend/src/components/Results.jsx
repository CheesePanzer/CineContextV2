import React, {useEffect, useRef, useState} from 'react';
import {api} from '../api/client.js';
import {STATUS_NAMES as statusNames} from '../app/constants.js';
import {getResearchTitle} from '../app/researchTitle.js';

const axisNames = {release: 'Release date', story: 'Story period', myth: 'Mythology', history: 'Historical context'};
const decisionNames = {accepted: 'Accepted', rejected: 'Excluded', needs_evidence: 'Insufficient evidence'};

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

function FurtherReading({runId, filmId}) {
  const [readings, setReadings] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    api.getFilmReadings(runId, filmId).then(data => {
      if (active) setReadings(data.readings);
    }).catch(() => {
      if (active) setFailed(true);
    });
    return () => { active = false; };
  }, [runId, filmId]);

  return <section className="film-readings" aria-label="Criticism and further reading">
    <h4>Criticism and further reading</h4>
    {failed && <p className="muted">Articles could not load. Reopen the film details to retry.</p>}
    {readings === null && !failed && <p className="muted">Looking for articles…</p>}
    {readings?.length === 0 && <p className="muted">No matching articles found.</p>}
    {!!readings?.length && <ul>
      {readings.map(reading => <li key={reading.url}>
        <small className="muted">{reading.publication}{reading.author ? ' · ' + reading.author : ''}</small>
        <a href={sourceLink(reading.url)} target="_blank" rel="noreferrer">{reading.title} ↗</a>
        {reading.summary && <p>{reading.summary}</p>}
      </li>)}
    </ul>}
  </section>;
}

function FilmDetail({run, filmId, storyEvents, onClose}) {
  const [detail, setDetail] = useState(null);
  const [ratings, setRatings] = useState(null);
  const [error, setError] = useState('');
  const [ratingError, setRatingError] = useState('');
  useEffect(() => {
    let active = true;
    let timer;
    api.getFilm(run.run_id, filmId).then(data => {
      if (active) setDetail(data);
    }).catch(problem => { if (active) setError(problem.message); });
    async function readRatings(attempt = 0) {
      try {
        const data = await api.getRatings(filmId);
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
      <FurtherReading runId={run.run_id} filmId={filmId} />
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
  const year = label.match(/^(?:公元)?(\d{3,4})(?=\s*(?:年|年代|$|[–—-]))/);
  if (year) return Number(year[1]);
  const englishCentury = label.match(/^(?:(early|mid|late)[ -])?(\d{1,2})(?:st|nd|rd|th)[ -]century\b/i);
  if (englishCentury) {
    const offset = {early: 0, mid: 40, late: 70};
    return (Number(englishCentury[2]) - 1) * 100 + (offset[englishCentury[1]?.toLowerCase()] ?? 0) + 1;
  }
  const englishYear = label.match(/^(\d{3,4})(?=s\b|[–—-]|\s|$)/);
  return englishYear ? Number(englishYear[1]) : Number.isInteger(event.sort_year) ? event.sort_year : null;
}

export default function Results({run, onRetryGuide, retryingGuide, onReviewNearby}) {
  const [axis, setAxis] = useState(() => run.constraints.axis || 'viewing');
  const [zoom, setZoom] = useState(100);
  const [filmId, setFilmId] = useState(null);
  const timelineViewport = useRef(null);
  const detailPanel = useRef(null);
  const study = run.study || {};
  const films = new Map((study.films || []).map(film => [film.id, film]));
  const evidence = new Map((study.evidence || []).map(item => [item.id, item]));
  const views = study.timeline_views || {};
  const guide = run.guide;
  const resultStatus = run.status === 'partial' && guide
    ? `${films.size} of ${run.constraints.count} films` : statusNames[run.status];
  const order = (guide?.viewing_order ?? [...films.keys()]).map(id => films.get(id)).filter(Boolean);
  const reasons = new Map((guide?.film_guides || []).map(item => [item.film_id, item.reason]));
  const decisions = new Map((study.decisions || []).map(item => [item.film_id, item]));
  const hasPreferences = Boolean(run.constraints.preferred_genres?.length || run.constraints.preferred_styles?.length);
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
  for (const key of ['history', 'myth']) {
    if (views[key]?.length || run.constraints.axis === key) tabs[key] = axisNames[key];
  }
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
    <div className="section-head"><span className="kicker">Viewing guide</span><span className="pill">{resultStatus}</span></div>
    <h2>{getResearchTitle(run.constraints)}</h2>
    <details className="original-question">
      <summary>Original question</summary>
      <p>{run.constraints.topic}</p>
    </details>
    {guide?.introduction && <p className="intro">{guide.introduction}</p>}
    {run.error && <p className="notice" role="alert">{run.error}</p>}
    {run.status === 'partial' && !guide && run.progress.at(-1) && <p className="notice">{run.progress.at(-1).message}</p>}
    {!guide && (study.films?.length ?? 0) > 0 && ['partial', 'failed', 'interrupted'].includes(run.status) &&
      <button className="button retry-guide" type="button" disabled={retryingGuide} onClick={onRetryGuide}>
        {retryingGuide ? 'Retrying guide…' : 'Retry guide generation'}
      </button>}
    {(study.limitations || []).map(note => <p className="notice" key={note}>{note}</p>)}
    {onReviewNearby && <button className="quiet-button" type="button" onClick={onReviewNearby}>
      Review criteria for nearby settings
    </button>}
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
          : nodes[axis] && 'Scroll sideways. Select a poster for details.'}</p>
        {nodes[axis]?.length > 0 && <div className="timeline-zoom" role="group" aria-label="Timeline zoom">
          <button type="button" aria-label="Zoom out" disabled={zoom === 100} onClick={() => setZoom(zoom - 25)}>−</button>
          <output aria-label="Zoom level">{zoom}%</output>
          <button type="button" aria-label="Zoom in" disabled={zoom === 225} onClick={() => setZoom(zoom + 25)}>+</button>
          <button type="button" aria-label="Reset timeline zoom" disabled={zoom === 100} onClick={() => setZoom(100)}>Reset</button>
        </div>}
      </div>
      {nodes[axis]?.length > 0 && <div className="film-track-viewport" ref={timelineViewport} tabIndex={0} aria-label="Scrollable film timeline">
        <ol className="film-track" style={{'--zoom': zoom / 100}}>
          {nodes[axis].map(({film, period, events}) => <li className="film-node" key={film.id}>
            <div className="film-node-period">{period}</div>
            <div className="film-node-content">
              <TimelineFilm film={film} selected={film.id === filmId} onOpen={openFilm} />
              {run.constraints.setting_match === 'nearby' && decisions.get(film.id)?.relevance === 'secondary' &&
                <small className="muted">Related setting</small>}
              {hasPreferences && decisions.get(film.id)?.preference_fit === 1 &&
                <small className="muted">Matches some of your preferences</small>}
              <p>{reasons.get(film.id)?.text || decisions.get(film.id)?.reason}</p>
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
      {!nodes[axis] && (views[axis]?.length ? <ol className="timeline">
        {views[axis].map(event => <li key={event.id}>
          <div className="time">{event.period}</div>
          <div className="event">
            <strong>{event.label}</strong>
            {event.version && <small>{event.version}</small>}
            {event.relation && <small>{event.relation}</small>}
            <Sources ids={event.evidence_ids} evidence={evidence} />
            <div className="timeline-films">
              {event.film_ids.filter(id => films.has(id)).map(id =>
                <TimelineFilm key={id} film={films.get(id)} selected={id === filmId} onOpen={openFilm} />)}
            </div>
          </div>
        </li>)}
      </ol> : <p className="muted">Available sources did not establish a timeline for this view.</p>)}
      {selected && <div className="timeline-details" ref={detailPanel} tabIndex={-1}>
        <FilmDetail key={filmId} run={run} filmId={filmId} storyEvents={selected.events} onClose={() => setFilmId(null)} />
      </div>}
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
