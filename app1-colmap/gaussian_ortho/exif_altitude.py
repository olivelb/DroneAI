import os
from exif import Image as ExifImage

def extract_exif_altitudes(images_dir):
    """
    Extract GPS altitude from all images in a directory.
    Returns a dict: {filename: altitude (float or None)}
    """
    altitudes = {}
    for fname in os.listdir(images_dir):
        if not fname.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
            continue
        try:
            with open(os.path.join(images_dir, fname), "rb") as f:
                img = ExifImage(f)
            if hasattr(img, "gps_altitude") and img.gps_altitude is not None:
                alt = float(img.gps_altitude)
                # If gps_altitude_ref exists and is 1, it's below sea level
                if hasattr(img, "gps_altitude_ref") and img.gps_altitude_ref == 1:
                    alt = -alt
                altitudes[fname] = alt
            else:
                altitudes[fname] = None
        except Exception:
            altitudes[fname] = None
    return altitudes
