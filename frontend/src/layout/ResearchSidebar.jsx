import React from 'react';
import {NavLink, useLocation, useNavigate} from 'react-router-dom';
import {STATUS_NAMES, TACC_STATUS_NAMES} from '../app/constants.js';
import {getResearchTitle} from '../app/researchTitle.js';
import TaccJobTiming from '../components/TaccJobTiming.jsx';
import {TERMINAL_STATUSES} from '../hooks/useRunPolling.js';

export default function ResearchSidebar({
  health, runs, onDelete, open, onClose, onSubmitTacc, submittingTacc,
  healthFailed, checkingHealth, onRefreshHealth, restartingTacc,
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const selectedId = location.pathname.match(/^\/research\/([^/]+)/)?.[1];
  const modelName = health?.model_mode === 'mock' ? 'Local demo' : health?.model_id || 'Model';
  const lastStatus = TACC_STATUS_NAMES[health?.model_status] || (health?.status === 'ok' ? 'App ready' : 'Connecting');
  const modelStatus = healthFailed ? 'App connection unavailable' : lastStatus;

  function openRun(item) {
    navigate(TERMINAL_STATUSES.has(item.status)
      ? `/research/${item.run_id}` : `/research/${item.run_id}/progress`);
    onClose();
  }

  return <>
    <button className={`drawer-backdrop ${open ? 'visible' : ''}`} aria-label="Close navigation" onClick={onClose} />
    <aside className={`research-sidebar ${open ? 'open' : ''}`} aria-label="Research history">
      <div className="sidebar-brand">
        <NavLink to="/research/new" onClick={onClose}>CineContext</NavLink>
        <span>Film research archive</span>
      </div>
      <NavLink className="new-research" to="/research/new" onClick={onClose}><span aria-hidden="true">+</span> New research</NavLink>
      <div className="history-heading">Recent research</div>
      <nav className="run-history" aria-label="Recent research runs">
        {runs.length === 0 && <p className="sidebar-empty">Your research runs will appear here.</p>}
        {runs.map(item => {
          const title = getResearchTitle(item);
          return <div className={`history-item ${selectedId === item.run_id ? 'selected' : ''}`} key={item.run_id}>
            <button className="history-link" onClick={() => openRun(item)}>
              <span className="history-topic">{title}</span>
              <span className="history-status"><i className={`status-dot ${item.status}`} />{STATUS_NAMES[item.status] || item.status}</span>
            </button>
            <button className="history-delete" type="button" disabled={!TERMINAL_STATUSES.has(item.status)}
              aria-label={`Delete ${title}`} title={TERMINAL_STATUSES.has(item.status) ? 'Delete research' : 'Active research cannot be deleted'}
              onClick={() => onDelete(item)}>×</button>
          </div>;
        })}
      </nav>
      <footer className="sidebar-footer">
        <span className="provider-dot" aria-hidden="true" />
        <div className="sidebar-model">
          <strong>{modelName}</strong>
          <div role="status">
            <small>{modelStatus}</small>
            {healthFailed && health && <span className="sidebar-model-note">Last known: {lastStatus}</span>}
          </div>
          {!healthFailed && <div className="sidebar-model-timing"><TaccJobTiming health={health} /></div>}
          {(submittingTacc || (!healthFailed && health?.tacc_resubmit_enabled && health.model_status === 'stopped')) &&
            <button className="sidebar-tacc-submit" type="button" disabled={submittingTacc} onClick={() => onSubmitTacc()}>
              {restartingTacc ? 'Replacing TACC job…' : submittingTacc ? 'Requesting GPUs…' : 'Request TACC GPUs'}
            </button>}
          {!submittingTacc && !healthFailed && health?.tacc_resubmit_enabled
            && ['ready', 'starting', 'queued'].includes(health.model_status) &&
            <button className="sidebar-tacc-submit" type="button"
              title="End the current job and request a new 1-hour allocation" onClick={() => onSubmitTacc(true)}>
              Restart · 1 hour
            </button>}
        </div>
        <button className="sidebar-status-refresh" type="button" aria-label="Refresh model status"
          title={checkingHealth ? 'Checking model status' : 'Refresh model status'}
          disabled={checkingHealth || submittingTacc} onClick={onRefreshHealth}>
          <span aria-hidden="true">{checkingHealth ? 'Checking' : 'Refresh'}</span>
        </button>
      </footer>
    </aside>
  </>;
}
