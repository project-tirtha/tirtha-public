import { describe, expect, it } from 'vitest';

import { foldForSearch, siteMatches } from './search';

describe('foldForSearch', () => {
  it('ignores case and Latin diacritics', () => {
    expect(foldForSearch('Koṇārka')).toBe('konarka');
    expect(foldForSearch('  CAFÉ ')).toBe('cafe');
  });

  it('treats precomposed and decomposed spellings as the same', () => {
    expect(foldForSearch('é')).toBe(foldForSearch('é'));
  });

  it('keeps Devanagari and Odia vowel signs, which are letters, not accents', () => {
    expect(foldForSearch('मंदिर')).toBe('मंदिर'.normalize('NFC'));
    expect(foldForSearch('ମନ୍ଦିର')).toBe('ମନ୍ଦିର'.normalize('NFC'));
  });

  it('tolerates null and numbers', () => {
    expect(foldForSearch(null)).toBe('');
    expect(foldForSearch(42)).toBe('42');
  });
});

describe('siteMatches', () => {
  const site = {
    name: 'Konark Sun Temple',
    location: 'Purī, Odisha',
    alternateNames: ['କୋଣାର୍କ ସୂର୍ଯ୍ୟ ମନ୍ଦିର', 'Koṇārka'],
  };

  it('matches the name, the place and any alternate name', () => {
    expect(siteMatches(site, foldForSearch('sun temple'))).toBe(true);
    expect(siteMatches(site, foldForSearch('Puri'))).toBe(true);
    expect(siteMatches(site, foldForSearch('କୋଣାର୍କ'))).toBe(true);
    expect(siteMatches(site, foldForSearch('konarka'))).toBe(true);
  });

  it('does not match an empty query or an unrelated one', () => {
    expect(siteMatches(site, '')).toBe(false);
    expect(siteMatches(site, foldForSearch('Lingaraj'))).toBe(false);
  });

  it('works for a site without alternate names', () => {
    expect(siteMatches({ name: 'Lingaraj', location: null }, 'lingaraj')).toBe(true);
  });
});
