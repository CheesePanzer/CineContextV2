export const RESEARCH_DRAFT_KEY = 'cinecontext.research-draft.v1';
export const EMPTY_DRAFT = {
  prompt: '', constraints: null, clarification: '', referenceMatch: null,
  preliminaryAnalysis: '', suggestedThemes: [], suggestedStyles: [],
};

export function loadDraft() {
  try {
    const value = JSON.parse(sessionStorage.getItem(RESEARCH_DRAFT_KEY));
    if (!value || typeof value.prompt !== 'string') return {...EMPTY_DRAFT};
    if (!Object.hasOwn(value, 'referenceMatch')) return {...EMPTY_DRAFT, prompt: value.prompt};
    return {...EMPTY_DRAFT, ...value};
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
