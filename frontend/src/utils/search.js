/**
 * Text folded for matching, not for display.
 *
 * `toLowerCase` alone meant "Konark" never matched "Koṇārka" and a query typed
 * with a precomposed "é" missed a name stored with a combining accent (or the
 * other way round -- the two are different code points until normalised).
 *
 * NFD splits a letter from its accents; the accents are then dropped. Only
 * marks that follow a *Latin* letter, though: in Devanagari and Odia the vowel
 * signs and the virama are combining marks too, and stripping those would make
 * "मंदिर" match strings that are not the same word. Those scripts are compared
 * NFC-normalised, which still unifies the precomposed and decomposed spellings
 * of nukta letters.
 */
export function foldForSearch(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .normalize('NFD')
    .replace(/(\p{Script=Latin})\p{M}+/gu, '$1')
    .normalize('NFC')
    .toLocaleLowerCase('en')
    .trim();
}

/**
 * Every string a site can be found by: its name, its place, and any alternate
 * or local names the API supplies. The sites API returns none today; the field
 * is read defensively so a server that adds `alternate_names` is searched
 * without a client change.
 */
export function searchableText(site) {
  if (!site) return [];
  const alternates = Array.isArray(site.alternateNames) ? site.alternateNames : [];
  return [site.name, site.location, ...alternates].filter(Boolean).map(foldForSearch);
}

export function siteMatches(site, foldedQuery) {
  if (!foldedQuery) return false;
  return searchableText(site).some((text) => text.includes(foldedQuery));
}
