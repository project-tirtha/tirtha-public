/**
 * The two labels a pin can carry, read off one address object.
 *
 * `getReadableLocationName` writes the site panel's location line and
 * `formatGeocodedAddress` the "pinned at" line in the request form. They are
 * deliberately different lengths, so the cases below are not "do they match"
 * but "do they still agree about which Nominatim field holds the street, the
 * locality and the name" -- the question two separate key lists got wrong.
 */
import { describe, expect, it } from 'vitest';

import { formatGeocodedAddress, getReadableLocationName } from './geocoding';

const coords = { lat: 19.805, lng: 85.818 };

describe('the two pin labels', () => {
  it('both read `pedestrian` as the street', () => {
    /*
      The divergence that started this. Nominatim answers a pin on Puri's
      Grand Road with `pedestrian` and no `road`; only the panel's key list
      knew that, so the form dropped the street and named the town instead --
      the one part that tells two pins in Puri apart.
    */
    const data = {
      address: { pedestrian: 'Grand Road', city: 'Puri', state: 'Odisha', country: 'India' },
    };

    expect(getReadableLocationName(data, coords)).toBe('Grand Road, Puri, Odisha, India');
    expect(formatGeocodedAddress(data, coords)).toBe('Grand Road, Puri, Odisha, India');
  });

  it('both read `city_district` as the locality', () => {
    const data = {
      address: { city_district: 'Chandanpur', city: 'Puri', state: 'Odisha', country: 'India' },
    };

    expect(getReadableLocationName(data, coords)).toBe('Chandanpur, Puri, Odisha, India');
    expect(formatGeocodedAddress(data, coords)).toBe('Chandanpur, Puri, Odisha, India');
  });

  it('both name a pin a `garden` tag is the only name for', () => {
    // And here they legitimately differ in length: the panel keeps the name
    // and the administrative tail, the form also keeps the street.
    const data = {
      address: {
        garden: 'Nandan Kanan',
        road: 'Temple Road',
        city: 'Puri',
        state: 'Odisha',
        country: 'India',
      },
    };

    expect(getReadableLocationName(data, coords)).toBe('Nandan Kanan, Puri, Odisha, India');
    expect(formatGeocodedAddress(data, coords)).toBe(
      'Nandan Kanan, Temple Road, Puri, Odisha, India',
    );
  });

  it('both let a named place outrank the building it sits in', () => {
    // `building` is often the street address in disguise, so neither label
    // may prefer it to a name someone would actually use for the place.
    const data = {
      address: {
        temple: 'Jagannath Temple',
        building: 'yes',
        road: 'Grand Road',
        city: 'Puri',
        state: 'Odisha',
        country: 'India',
      },
    };

    expect(getReadableLocationName(data, coords)).toBe('Jagannath Temple, Puri, Odisha, India');
    expect(formatGeocodedAddress(data, coords)).toBe(
      'Jagannath Temple, Grand Road, Puri, Odisha, India',
    );
  });
});
