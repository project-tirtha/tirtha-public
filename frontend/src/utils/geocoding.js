export function extractCoordsFromGoogleMapsUrl(url) {
  if (!url) return null;
  
  let decodedUrl = url;
  try {
    decodedUrl = decodeURIComponent(url);
  } catch {
    // ignore
  }

  // Format 1: Query param q=lat,lng
  const qRegex = /[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)/;
  const qMatch = decodedUrl.match(qRegex);
  if (qMatch) {
    return { lat: parseFloat(qMatch[1]), lng: parseFloat(qMatch[2]) };
  }

  // Format 2: @lat,lng
  const atRegex = /@(-?\d+\.\d+),(-?\d+\.\d+)/;
  const atMatch = decodedUrl.match(atRegex);
  if (atMatch) {
    return { lat: parseFloat(atMatch[1]), lng: parseFloat(atMatch[2]) };
  }

  // Format 3: Raw coordinates in path like /place/18.497500,73.847500
  const pathRegex = /\/place\/(-?\d+\.\d+),(-?\d+\.\d+)/;
  const pathMatch = decodedUrl.match(pathRegex);
  if (pathMatch) {
    return { lat: parseFloat(pathMatch[1]), lng: parseFloat(pathMatch[2]) };
  }

  // Format 4: Any lat,lng pattern
  const coordRegex = /(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)/;
  const coordMatch = decodedUrl.match(coordRegex);
  if (coordMatch) {
    return { lat: parseFloat(coordMatch[1]), lng: parseFloat(coordMatch[2]) };
  }

  return null;
}

const geocodeCache = new Map();

export function formatCoordinates(coordinates) {
  if (!coordinates) return '';

  const lat = Number(coordinates.lat);
  const lng = Number(coordinates.lng);

  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return '';

  return `${lat.toFixed(6)}, ${lng.toFixed(6)}`;
}

export function isCoordinateLabel(value) {
  if (!value || typeof value !== 'string') return false;

  const clean = value.trim();
  return /^[-+]?\d+(?:\.\d+)?\s*,\s*[-+]?\d+(?:\.\d+)?$/.test(clean) || /°/.test(clean);
}

export function isGenericLocationLabel(value) {
  if (!value || typeof value !== 'string') return true;

  const clean = value.trim().toLowerCase();
  return !clean || clean === 'india' || clean === 'unknown location' || isCoordinateLabel(clean);
}

export function buildGoogleMapsSearchUrl({ placeName, location, coordinates }) {
  const readableParts = [placeName, location]
    .map((part) => (typeof part === 'string' ? part.trim() : ''))
    .filter(Boolean)
    .filter((part, index, parts) => parts.findIndex((candidate) => candidate.toLowerCase() === part.toLowerCase()) === index)
    .filter((part) => !isCoordinateLabel(part));

  const query = readableParts.length > 0 ? readableParts.join(', ') : formatCoordinates(coordinates);

  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
}

/*
  Nominatim will not answer forever, and a form waiting on it should not either.
*/
const GEOCODE_TIMEOUT_MS = 8000;

export async function reverseGeocode(lat, lng) {
  if (lat === null || lng === null || isNaN(lat) || isNaN(lng)) return null;

  const key = `${parseFloat(lat).toFixed(6)},${parseFloat(lng).toFixed(6)}`;
  if (geocodeCache.has(key)) return geocodeCache.get(key);

  const url = `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lng}&format=json&addressdetails=1`;

  try {
    /*
      No `User-Agent` header.

      There was one -- `Tirtha-Heritage-Explorer/1.0` -- put there to satisfy
      Nominatim's usage policy, which asks every caller to identify itself.
      `User-Agent` is a forbidden header name: `fetch` drops it silently rather
      than failing, so the request went out anonymous and the code read as
      though it complied. From a browser the only identification Nominatim
      actually receives is the `Referer`, which it sends on its own.

      This is one of the reasons for moving the lookup server-side, where the
      header can be set and the request can be rate-limited to the one call a
      second the policy asks for (internal design notes).
    */
    const response = await fetch(url, {
      headers: { 'Accept-Language': 'en' },
      signal: AbortSignal.timeout(GEOCODE_TIMEOUT_MS),
    });

    if (!response.ok) return null;

    const data = await response.json();
    geocodeCache.set(key, data);
    return data;
  } catch {
    // A failed lookup is not an error the visitor needs: the pin's coordinates
    // are shown instead, and every field it would have filled is editable.
    return null;
  }
}

/*
  One reading of Nominatim's `address` object, shared by both labels below.

  There were two, and they had drifted. `resolveLocationDetails` read
  `pedestrian` for the street and `city_district`/`subdistrict` for the
  locality; `formatGeocodedAddress` read none of them and ordered `building`
  differently. So a pin on Puri's Grand Road was "Grand Road, Puri, Odisha,
  India" on the site panel and "Puri, Odisha, India" under the same pin in the
  request form -- one coordinate, two labels, and the next correction would
  land on one key list and leave the other as it was.

  The two labels below are still different lengths, on purpose; what they must
  not disagree about is which field holds the street.
*/
function extractAddressParts(address) {
  const addr = address || {};

  const firstOf = (keys) => {
    for (const key of keys) {
      if (addr[key]) return addr[key];
    }
    return '';
  };

  // A name for the thing itself -- what someone would call the place.
  const poiName = firstOf([
    'temple', 'monument', 'historic', 'tourism', 'amenity', 'attraction',
    'leisure', 'shop', 'hill', 'natural', 'religion', 'place_of_worship',
  ]);

  // The open place around it. A garden or a square names a pin that none of
  // the keys above will, so it stands in when they are all absent.
  const landmarkName = firstOf(['landmark', 'park', 'square', 'common', 'village_green', 'garden']);

  // Weaker than either: a building tag is often the street address in
  // disguise, so it speaks only when nothing above has named the place.
  const buildingName = firstOf(['building', 'house', 'office', 'construction']);

  const streetName = firstOf(['road', 'street', 'footway', 'path', 'pedestrian']);
  const houseNumber = addr.house_number || '';
  const streetAddress = [houseNumber, streetName].filter(Boolean).join(' ');

  const localityName = firstOf([
    'neighbourhood', 'suburb', 'village', 'hamlet', 'subdivision', 'city_district', 'subdistrict',
  ]);

  const city = firstOf(['city', 'town', 'county', 'municipality']);
  const state = addr.state || '';
  const country = addr.country || '';

  return { poiName, landmarkName, buildingName, streetAddress, localityName, city, state, country };
}

/**
 * The pin's place as a name, and the administrative tail under it.
 *
 * Keeps only the most specific name the geocoder gave, deliberately: this is
 * the one line under a site's title on the panel, where the rest of a postal
 * address would crowd out the name it is there to place.
 */
export function resolveLocationDetails(data, coordinates) {
  if (!data) {
    return {
      title: coordinates ? `${coordinates.lat.toFixed(6)}, ${coordinates.lng.toFixed(6)}` : 'Unknown Location',
      subtitle: '',
      coordinates: coordinates,
      isGeocoded: false
    };
  }

  const addr = data.address || {};
  const { poiName, landmarkName, buildingName, streetAddress, localityName, city, state, country } =
    extractAddressParts(addr);

  // Determine Title according to priority order
  let title = '';
  let subtitleParts = [];

  const specificName = poiName || landmarkName || buildingName || streetAddress || localityName;

  if (specificName) {
    title = specificName;
  } else {
    // If only City, State, Country is available
    const parts = [city, state, country].filter(Boolean);
    if (parts.length > 0) {
      title = parts[0];
      subtitleParts = parts.slice(1);
    } else if (coordinates) {
      title = `${coordinates.lat.toFixed(6)}, ${coordinates.lng.toFixed(6)}`;
    } else {
      title = data.display_name || 'Selected Location';
    }
  }

  // Determine Subtitle
  if (subtitleParts.length === 0) {
    // Subtitle should be City, State, Country
    const parts = [city, state, country].filter(Boolean);
    // If the title is one of these, filter it out
    subtitleParts = parts.filter(part => part.toLowerCase() !== title.toLowerCase());
  }

  const subtitle = subtitleParts.join(', ');

  return {
    title: title.trim(),
    subtitle: subtitle.trim(),
    coordinates: coordinates,
    isGeocoded: true,
    rawAddress: addr
  };
}

export function getReadableLocationName(data, coordinates = null) {
  if (!data) return '';

  const details = resolveLocationDetails(data, coordinates);
  if (!details?.isGeocoded || isCoordinateLabel(details.title)) return '';

  return [details.title, details.subtitle]
    .filter(Boolean)
    .filter((part, index, parts) => parts.findIndex((candidate) => candidate.toLowerCase() === part.toLowerCase()) === index)
    .join(', ');
}

/**
 * Every part of the pin's address, from the name down to the country.
 *
 * Longer than `getReadableLocationName` on the same data, and meant to be:
 * this is the "Pinned at ..." line someone reads to check that the pin they
 * dragged sits on the site they mean, and the street is often the only part
 * that tells two pins in the same town apart.
 */
export function formatGeocodedAddress(data, fallbackCoords = null) {
  if (!data || !data.address) {
    if (fallbackCoords) {
      return `${fallbackCoords.lat.toFixed(6)}, ${fallbackCoords.lng.toFixed(6)}`;
    }
    return '';
  }

  const { poiName, landmarkName, buildingName, streetAddress, localityName, city, state, country } =
    extractAddressParts(data.address);

  // Narrowest first, the way the address would be read out.
  const parts = [
    poiName || buildingName,
    landmarkName,
    streetAddress,
    localityName,
    city,
    state,
    country,
  ];

  // Nominatim repeats itself -- a village that is also the locality, a garden
  // that is also the amenity -- so the same word arrives in two slots.
  const uniqueParts = [];
  parts.forEach(part => {
    const trimmed = part.trim();
    if (trimmed && !uniqueParts.some(p => p.toLowerCase() === trimmed.toLowerCase())) {
      uniqueParts.push(trimmed);
    }
  });

  if (uniqueParts.length === 0) {
    if (fallbackCoords) {
      return `${fallbackCoords.lat.toFixed(6)}, ${fallbackCoords.lng.toFixed(6)}`;
    }
    return data.display_name || '';
  }

  return uniqueParts.join(', ');
}
