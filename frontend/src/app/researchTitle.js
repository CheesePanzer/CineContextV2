export function getResearchTitle({research_title, theme} = {}) {
  for (const value of [research_title, theme]) {
    const title = value?.trim().replace(/[.…\s]+$/u, '');
    if (title) return title;
  }
  return 'Film research';
}
