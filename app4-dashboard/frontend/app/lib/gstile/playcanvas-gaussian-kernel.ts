/**
 * DroneGS/native use an unnormalized exp(-r²/2) kernel with 3-sigma support.
 * PlayCanvas raster UVs are in sqrt(8)*sigma units. Its default normExp removes
 * the tail and renormalizes the remaining alpha, leaking more background.
 * Override only this device's raster chunks; keep ordinary premultiplied OVER.
 */
export const nativeGaussianRasterChunks = (fragment: string, vertex: string) => {
  const replaceOnce = (source: string, anchor: string, replacement: string) => {
    if (source.split(anchor).length !== 2)
      throw new Error("Unsupported PlayCanvas Gaussian raster shader");
    return source.replace(anchor, replacement);
  };
  const ps = replaceOnce(
    replaceOnce(fragment,
      "return (exp(x * half(-4.0)) - EXP4) * INV_EXP4;",
      "return exp(x * half(-4.0) / half(uniform.gstileEdgeOpacity));"),
    "if (A > half(1.0)) {",
    "if (A > half(1.125)) {",
  );
  const vs = replaceOnce(vertex,
    "let clip = min(half(1.0),",
    "let clip = min(half(1.0606601717798212),",
  );
  // Adapt the alpha-threshold quad clipping as well, or opaque tails would be
  // cut off at the old radius. The 3-sigma outer support remains unchanged.
  const clipped = replaceOnce(vs,
    "log(alpha / alphaClipValue)",
    "log(alpha / alphaClipValue) * half(uniform.gstileEdgeOpacity)");
  const uniform = "uniform gstileEdgeOpacity: f32;\n";
  return { fragment: uniform + ps, vertex: uniform + clipped };
};
