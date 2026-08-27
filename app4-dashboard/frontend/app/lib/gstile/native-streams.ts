export type GsTileRgbaTextureSource =
  | Uint8Array
  | Uint16Array
  | Uint32Array
  | Float32Array;

type DisposableTexture = {
  destroy: () => void;
};

type StreamDescriptor = {
  format: number;
};

type StreamFormat = {
  getStream: (name: string) => StreamDescriptor | undefined;
};

type StreamCollection<TTexture extends DisposableTexture, TSize> = {
  textureDimensions: TSize & { x: number; y: number };
  textures: Map<string, TTexture>;
  createTexture: (
    name: string,
    format: number,
    size: TSize,
    data: GsTileRgbaTextureSource,
  ) => TTexture;
};

/** Square packing by default; incremental staging may match the arena width. */
export const gsTileTextureDimensions = (count: number, textureWidth?: number) => {
  if (!Number.isSafeInteger(count) || count < 1) {
    throw new Error("GSTile texture element count must be positive");
  }
  const width = textureWidth ?? Math.ceil(Math.sqrt(count));
  if (!Number.isSafeInteger(width) || width < 1) {
    throw new Error("GSTile texture width must be a positive safe integer");
  }
  const height = Math.ceil(count / width);
  if (!Number.isSafeInteger(width * height * 4)) {
    throw new Error("GSTile texture capacity overflows RGBA addressing");
  }
  return { width, height };
};

export const gsTileTextureElementCapacity = (count: number, textureWidth?: number) => {
  const { width, height } = gsTileTextureDimensions(count, textureWidth);
  return width * height;
};

/** Called on empty engine streams before the first native array is adopted. */
export const resizeGsTileStagingStreams = (
  streams: { textureDimensions: { x: number; y: number }; resize(width: number, height: number): void },
  count: number,
  textureWidth: number,
) => {
  const { width, height } = gsTileTextureDimensions(count, textureWidth);
  if (streams.textureDimensions.x !== width || streams.textureDimensions.y !== height) {
    streams.resize(width, height);
  }
};

/**
 * Install or replace RGBA stream textures adopting prepacked data.
 *
 * All inputs and descriptors are validated and all replacements are created
 * before the stream map changes, so a failed allocation leaves the resource
 * untouched.
 */
export const adoptGsTileNativeRgbaStreams = <
  TTexture extends DisposableTexture,
  TSize,
>(
  streams: StreamCollection<TTexture, TSize>,
  format: StreamFormat,
  names: readonly string[],
  sources: readonly GsTileRgbaTextureSource[],
) => {
  if (names.length === 0 || names.length !== sources.length) {
    throw new Error("GSTile native stream adoption shape is inconsistent");
  }
  const expectedLength =
    streams.textureDimensions.x * streams.textureDimensions.y * 4;
  const originals = names.map((name) => streams.textures.get(name));
  const descriptors = names.map((name) => format.getStream(name));
  if (
    !Number.isSafeInteger(expectedLength) ||
    expectedLength < 4 ||
    descriptors.some((descriptor) => !descriptor) ||
    sources.some((source) => source.length !== expectedLength)
  ) {
    throw new Error("GSTile native stream adoption inputs are inconsistent");
  }

  const replacements: TTexture[] = [];
  try {
    names.forEach((name, index) => {
      replacements.push(
        streams.createTexture(
          name,
          descriptors[index]!.format,
          streams.textureDimensions,
          sources[index],
        ),
      );
    });
  } catch (error) {
    replacements.forEach((texture) => texture.destroy());
    throw error;
  }

  names.forEach((name, index) => {
    streams.textures.set(name, replacements[index]);
    originals[index]?.destroy();
  });
};
