import {
  arrayOf,
  decoder,
  integerValue,
  nonEmptyString,
  objectWith,
} from "./contract-decoder";

export type DirectUploadFile = {
  file_id: string;
  name: string;
  size: number;
  s3_key: string;
  total_parts: number;
  status: string;
};

export type DirectUploadSession = {
  session_id: string;
  dataset: string;
  status: string;
  total: number;
  total_bytes: number;
  part_size: number;
  expires_at: string;
  files: DirectUploadFile[];
};

export type UploadResult = {
  total: number;
  completed: number;
  failed: number;
  status: string;
};

const uploadFile = objectWith({
  file_id: nonEmptyString,
  name: nonEmptyString,
  size: integerValue,
  s3_key: nonEmptyString,
  total_parts: integerValue,
  status: nonEmptyString,
});

export const parseDirectUploadSession = decoder<DirectUploadSession>(
  "dataset upload session",
  objectWith({
    session_id: nonEmptyString,
    dataset: nonEmptyString,
    status: nonEmptyString,
    total: integerValue,
    total_bytes: integerValue,
    part_size: integerValue,
    expires_at: nonEmptyString,
    files: arrayOf(uploadFile),
  }),
);

export const parseSignedUploadPart = decoder<{
  method: string;
  url: string;
  expires_in: number;
  part_number: number;
  expected_size: number;
}>(
  "signed upload part",
  objectWith({
    method: nonEmptyString,
    url: nonEmptyString,
    expires_in: integerValue,
    part_number: integerValue,
    expected_size: integerValue,
  }),
);

export const parseUploadResult = decoder<UploadResult>(
  "dataset upload completion",
  objectWith({
    total: integerValue,
    completed: integerValue,
    failed: integerValue,
    status: nonEmptyString,
  }),
);

export const parseUploadFileCompletion = decoder<{
  file_id: string;
  name: string;
  s3_key: string;
  size: number;
  etag: string;
  status: string;
}>(
  "dataset upload file completion",
  objectWith({
    file_id: nonEmptyString,
    name: nonEmptyString,
    s3_key: nonEmptyString,
    size: integerValue,
    etag: nonEmptyString,
    status: nonEmptyString,
  }),
);

export const parseUploadAbort = decoder<{
  session_id: string;
  status: string;
}>(
  "dataset upload abort",
  objectWith({
    session_id: nonEmptyString,
    status: nonEmptyString,
  }),
);
