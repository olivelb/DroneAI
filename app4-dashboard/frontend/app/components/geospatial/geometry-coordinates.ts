import type { Geometry, Position } from "geojson";

export interface EditableCoordinateVertex {
  index: number;
  label: string;
  longitude: number;
  latitude: number;
}

const samePosition = (left: Position, right: Position) =>
  left.length >= 2 &&
  right.length >= 2 &&
  left[0] === right[0] &&
  left[1] === right[1];

export const editableCoordinateVertices = (
  geometry: Geometry,
): EditableCoordinateVertex[] | null => {
  let coordinates: Position[];
  if (geometry.type === "Point") {
    coordinates = [geometry.coordinates];
  } else if (geometry.type === "LineString") {
    coordinates = geometry.coordinates;
  } else if (geometry.type === "Polygon" && geometry.coordinates.length > 0) {
    const ring = geometry.coordinates[0];
    coordinates =
      ring.length > 1 && samePosition(ring[0], ring[ring.length - 1])
        ? ring.slice(0, -1)
        : ring;
  } else {
    return null;
  }
  return coordinates.map((position, index) => ({
    index,
    label: geometry.type === "Point" ? "Point" : `Sommet ${index + 1}`,
    longitude: Number(position[0]),
    latitude: Number(position[1]),
  }));
};

const validatedPosition = (longitude: number, latitude: number): Position => {
  if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
    throw new RangeError("longitude must be between -180 and 180");
  }
  if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
    throw new RangeError("latitude must be between -90 and 90");
  }
  return [longitude, latitude];
};

export const updateEditableCoordinateVertex = (
  geometry: Geometry,
  index: number,
  longitude: number,
  latitude: number,
): Geometry => {
  const position = validatedPosition(longitude, latitude);
  if (geometry.type === "Point") {
    if (index !== 0) throw new RangeError("point vertex index must be zero");
    return { ...geometry, coordinates: position };
  }
  if (geometry.type === "LineString") {
    if (index < 0 || index >= geometry.coordinates.length) {
      throw new RangeError("line vertex index is out of range");
    }
    const coordinates = geometry.coordinates.map((current, currentIndex) =>
      currentIndex === index ? position : [...current],
    );
    return { ...geometry, coordinates };
  }
  if (geometry.type === "Polygon" && geometry.coordinates.length > 0) {
    const coordinates = geometry.coordinates.map((ring) =>
      ring.map((current) => [...current]),
    );
    const exterior = coordinates[0];
    const closed =
      exterior.length > 1 &&
      samePosition(exterior[0], exterior[exterior.length - 1]);
    const editableLength = closed ? exterior.length - 1 : exterior.length;
    if (index < 0 || index >= editableLength) {
      throw new RangeError("polygon vertex index is out of range");
    }
    exterior[index] = position;
    if (closed && index === 0) exterior[exterior.length - 1] = [...position];
    return { ...geometry, coordinates };
  }
  throw new TypeError(`geometry ${geometry.type} does not expose editable vertices`);
};
