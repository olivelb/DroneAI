# Dataset browse source and cost

Root and dataset catalogue entries remain scoped by organization and owner.
For an immutable dataset with a durable upload session, browse reads completed
file keys from PostgreSQL and includes its published manifest. It does not query
S3 to rediscover that catalogue. The current upload API accepts plain filenames;
the grouping helper also supports nested metadata keys without changing upload
admission rules. TIFF counts now agree with upload catalogue image counts.

Legacy datasets without upload metadata and mission folders use one recursive
S3 paginator. Immediate entries and descendant image counts are accumulated in
one pass, without a listing per child directory or a list of every descendant
key. Cost still scales with the objects traversed; this is not a precomputed
legacy directory index. Only direct entries and per-child counts are retained.

Authorization precedes metadata or object-storage browsing. SQL prefix filters
escape literal wildcard characters; keys outside the authorized prefix are
rejected. Tests cover two tenant/owner boundaries, zero S3 reads for catalogued
uploads, one traversal for 100 legacy directories, and lazy page consumption.
