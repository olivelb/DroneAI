# Post-upload image header admission

After multipart completion and ownership/size verification, JPEG, PNG, WebP,
classic TIFF and BigTIFF uploads must have the matching signature before the file
can become completed and the dataset can become ready. Navigation and text
sidecars retain their existing admission rules.

The probe reads at most 16 bytes through a conditional S3 Range GET with the ETag
observed by HEAD. The reader checks status, Content-Range, Content-Length, ETag and
actual bytes received, and always closes the response body. A transport failure
retains the completing intent for recovery. A mismatched signature marks the file
and session failed; normal failed-upload cleanup owns subsequent storage removal.

This is a lightweight format check, not full decoding, structural validation or
malware detection. Signature-prefixed malformed/polyglot files can still pass.

Qualification covers accepted/truncated/mismatched signatures, tenant-owned
finalization rejection, recoverable transport errors, and bounded conditional
reads. A pinned local MinIO admitted synthetic JPEG/PNG headers, rejected a PDF
header named JPG, read only 16 bytes from a 1 MiB object, and returned 412 for an
If-Match mismatch. The existing HTTP/S3 composition test exercises the normal
multipart-to-ready path with a synthetic JPEG signature.
