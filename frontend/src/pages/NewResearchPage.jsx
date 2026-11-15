import React, {useState} from 'react';
import {useNavigate, useOutletContext} from 'react-router-dom';
import {api} from '../api/client.js';
import {TACC_STATUS_NAMES, TACC_WAITING_STATES} from '../app/constants.js';
import {loadDraft, saveDraft} from '../state/researchDraft.js';

const EXAMPLES = [
  ['Norse mythology', 'Find five films inspired by Norse mythology and show how they relate to the mythological timeline.'],
  ['State surveillance', 'Find three European films about state surveillance released between 1980 and 2020.'],
  ['Political memory', 'Find three films about Romanian political memory and show their historical context.'],
];

export default function NewResearchPage() {
  const initial = loadDraft();
  const [prompt, setPrompt] = useState(initial.prompt);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const {health} = useOutletContext();
  const navigate = useNavigate();
  const waitingForModel = TACC_WAITING_STATES.has(health?.model_status);

  function updatePrompt(value) {
    setPrompt(value);
    saveDraft({prompt: value, constraints: null, clarification: ''});
  }

  async function analyze(event) {
    event.preventDefault();
    if (busy || waitingForModel || !prompt.trim()) return;
    setBusy(true);
    setError('');
    saveDraft({prompt, constraints: null, clarification: ''});
    try {
      const parsed = await api.parseRequest(prompt);
      saveDraft({prompt, constraints: parsed.constraints, clarification: parsed.clarification,
        referenceMatch: parsed.reference_match ?? null, preliminaryAnalysis: parsed.preliminary_analysis,
        suggestedThemes: parsed.suggested_themes, suggestedStyles: parsed.suggested_styles});
      navigate('/research/criteria');
    } catch (problem) { setError(problem.message); }
    finally { setBusy(false); }
  }

  function onKeyDown(event) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) event.currentTarget.form?.requestSubmit();
  }

  return <main className="new-page page-frame">
    <div className="new-intro">
      <span className="eyebrow">Film, history, and myth</span>
      <h1>What should we explore?</h1>
      <p>Ask a research question. If it refers to a named work, CineContext can look up its setting before you review the criteria.</p>
    </div>
    <form className="composer-wrap" onSubmit={analyze}>
      <div className="prompt-examples" aria-label="Example research questions">
        {EXAMPLES.map(([label, value]) => <button type="button" key={label} disabled={busy} onClick={() => updatePrompt(value)}>{label}</button>)}
      </div>
      <div className="composer">
        <label htmlFor="research-prompt">Research question</label>
        <textarea id="research-prompt" value={prompt} onChange={event => updatePrompt(event.target.value)} onKeyDown={onKeyDown}
          placeholder="Which films can help me understand…" rows="5" required maxLength="500" disabled={busy} autoFocus />
        <div className="composer-actions">
          <span><i className="provider-dot" />{health?.model_mode === 'mock' ? 'Local demo' : health?.model_id || 'Model'}</span>
          <span className="keyboard-hint">Ctrl/⌘ + Enter</span>
          <button className="primary-button" type="submit" disabled={busy || waitingForModel || !prompt.trim()}>{busy ? 'Analyzing…' : 'Analyze question'} <b aria-hidden="true">→</b></button>
        </div>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {waitingForModel && <p className="capability-note" role="status">{TACC_STATUS_NAMES[health.model_status]}.</p>}
      {health && (!health.metadata_enabled || !health.web_enabled) && <p className="capability-note">
        {!health.metadata_enabled && 'Film metadata is unavailable. '}{!health.web_enabled && 'Web search is unavailable.'}
      </p>}
    </form>
  </main>;
}
