import React from 'react';
import {NavLink, useLocation, useNavigate} from 'react-router-dom';
import {MODEL_NAME, STATUS_NAMES} from '../app/constants.js';
import {TERMINAL_STATUSES} from '../hooks/useRunPolling.js';

export default function ResearchSidebar({health, runs, onDelete, open, onClose}) {
  const location = useLocation();
  const navigate = useNavigate();
  const selectedId = location.pathname.match(/^\/research\/([^/]+)/)?.[1];

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
        {runs.map(item => <div className={`history-item ${selectedId === item.run_id ? 'selected' : ''}`} key={item.run_id}>
          <button className="history-link" onClick={() => openRun(item)}>
            <span className="history-topic">{item.topic}</span>
            <span className="history-status"><i className={`status-dot ${item.status}`} />{STATUS_NAMES[item.status] || item.status}</span>
          </button>
          <button className="history-delete" type="button" disabled={!TERMINAL_STATUSES.has(item.status)}
            aria-label={`Delete ${item.topic}`} title={TERMINAL_STATUSES.has(item.status) ? 'Delete research' : 'Active research cannot be deleted'}
            onClick={() => onDelete(item)}>×</button>
        </div>)}
      </nav>
      <footer className="sidebar-footer">
        <span className="provider-dot" />
        <div><strong>{MODEL_NAME}</strong><small>{health?.status === 'ok' ? 'TACC ready' : 'Connecting to TACC'}</small></div>
      </footer>
    </aside>
  </>;
}
