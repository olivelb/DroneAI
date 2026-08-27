import { planLinearTextureCopies, type LinearTextureCopy } from "./merged-arena";

type TextureShape = { width: number; height: number };
type CopyDestination<T> = TextureShape & {
  copy(source: T, options: LinearTextureCopy): boolean;
};

/** Copy one range in stream order, sharing geometry only for identical shapes. */
export function copyGsTileTextureRange<T extends TextureShape>(
  source: {
    format: { resourceStreams: readonly { name: string }[] };
    getTexture(name: string): T | null | undefined;
  },
  destination: {
    getTexture(name: string): CopyDestination<T> | null | undefined;
  },
  sourceOffset: number,
  destinationOffset: number,
  count: number,
): void {
  // One entry, local to this range: no retained cut-sized cache or stale offsets.
  let geometry: {
    sourceWidth: number;
    sourceHeight: number;
    destinationWidth: number;
    destinationHeight: number;
    copies: LinearTextureCopy[];
  } | undefined;

  for (const stream of source.format.resourceStreams) {
    const sourceTexture = source.getTexture(stream.name);
    const destinationTexture = destination.getTexture(stream.name);
    if (!sourceTexture || !destinationTexture) {
      throw new Error(`GSTile arena stream ${stream.name} is unavailable`);
    }
    const sourceWidth = sourceTexture.width;
    const sourceHeight = sourceTexture.height;
    const destinationWidth = destinationTexture.width;
    const destinationHeight = destinationTexture.height;
    if (
      !geometry ||
      geometry.sourceWidth !== sourceWidth ||
      geometry.sourceHeight !== sourceHeight ||
      geometry.destinationWidth !== destinationWidth ||
      geometry.destinationHeight !== destinationHeight
    ) {
      geometry = {
        sourceWidth,
        sourceHeight,
        destinationWidth,
        destinationHeight,
        copies: planLinearTextureCopies(
          sourceWidth,
          sourceHeight,
          sourceOffset,
          destinationWidth,
          destinationHeight,
          destinationOffset,
          count,
        ),
      };
    }
    // PlayCanvas 2.21.4 reads but does not mutate these options. Keep command order.
    for (const options of geometry.copies) {
      if (!destinationTexture.copy(sourceTexture, options)) {
        throw new Error(`GSTile arena stream ${stream.name} copy failed`);
      }
    }
  }
}
