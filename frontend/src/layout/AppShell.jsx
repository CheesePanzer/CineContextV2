import React, {useEffect, useState} from 'react';
import {Outlet, useLocation, useNavigate} from 'react-router-dom';
import {api} from '../api/client.js';
import ResearchSidebar from './ResearchSidebar.jsx';

export default function AppShell() {
  const [health, setHealth] = useState(null);
  const [runs, setRuns] = useState([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [shellError, setShellError] = useState('');
  const location = useLocation();
  const navigate = useNavigate();

  async function refreshRuns() {
    try { setRuns((await api.listRuns()).runs); } catch { /* Page-level requests retain actionable errors. */ }
  }

  useEffect(() => {
    api.health().then(setHealth).catch(() => setShellError('Cannot check the server status.'));
    refreshRuns();
  }, []);
  useEffect(() => { setDrawerOpen(false); }, [location.pathname]);

  async function removeRun(item) {
    if (!confirm(`Delete “${item.topic}”? This cannot be undone.`)) return;
    try {
      await api.deleteRun(item.run_id);
      await refreshRuns();
      if (location.pathname.includes(item.run_id)) navigate('/research/new');
    } catch (problem) { setShellError(problem.message); }
  }

  return <div className="app-shell">
    <ResearchSidebar health={health} runs={runs} onDelete={removeRun} open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    <div className="app-main">
      <header className="mobile-header">
        <button aria-label="Open research history" onClick={() => setDrawerOpen(true)}>☰</button>
        <span>CineContext</span>
      </header>
      {shellError && <div className="shell-alert" role="alert">{shellError}<button aria-label="Dismiss" onClick={() => setShellError('')}>×</button></div>}
      <Outlet context={{health, refreshRuns, removeRun}} />
      {health?.metadata_enabled && <footer className="tmdb-credit">
        <a href="https://www.themoviedb.org/" target="_blank" rel="noreferrer">TMDB</a>
        <span>This product uses the TMDB API but is not endorsed or certified by TMDB.</span>
      </footer>}
    </div>
  </div>;
}
