const CORPORATE_MARKERS = /\(주\)|㈜|주식회사/g;

// Mirrors news-graph-builder's common/normalize.py; company_aliases stores names in this form.
export function normalizeCompanyName(text: string): string {
  return text.normalize("NFKC").replace(CORPORATE_MARKERS, "").replace(/\s+/g, "").toLowerCase();
}
