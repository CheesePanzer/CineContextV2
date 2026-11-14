import React from 'react';
import {Navigate, Route, Routes} from 'react-router-dom';
import AppShell from '../layout/AppShell.jsx';
import NewResearchPage from '../pages/NewResearchPage.jsx';
import CriteriaPage from '../pages/CriteriaPage.jsx';
import ProgressPage from '../pages/ProgressPage.jsx';
import ResultPage from '../pages/ResultPage.jsx';

export default function App() {
  return <Routes>
    <Route element={<AppShell />}>
      <Route index element={<Navigate to="/research/new" replace />} />
      <Route path="research/new" element={<NewResearchPage />} />
      <Route path="research/criteria" element={<CriteriaPage />} />
      <Route path="research/:runId/progress" element={<ProgressPage />} />
      <Route path="research/:runId" element={<ResultPage />} />
      <Route path="*" element={<Navigate to="/research/new" replace />} />
    </Route>
  </Routes>;
}
