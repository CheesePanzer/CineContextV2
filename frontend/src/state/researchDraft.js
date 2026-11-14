export const RESEARCH_DRAFT_KEY = 'cinecontext.research-draft.v1';
export const EMPTY_DRAFT = {prompt: '', constraints: null, clarification: ''};

export function loadDraft() {
  try {
    const value = JSON.parse(sessionStorage.getItem(RESEARCH_DRAFT_KEY));
    return value && typeof value.prompt === 'string'
      ? {...EMPTY_DRAFT, ...value}
      : {...EMPTY_DRAFT};
  } catch {
    return {...EMPTY_DRAFT};
  }
}

export function saveDraft(draft) {
  sessionStorage.setItem(RESEARCH_DRAFT_KEY, JSON.stringify({...EMPTY_DRAFT, ...draft}));
}

export function clearDraft() {
  sessionStorage.removeItem(RESEARCH_DRAFT_KEY);
}
