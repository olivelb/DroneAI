export class ResponseContractError extends Error {
  constructor(contract: string, path: string, expected: string) {
    super(`Invalid ${contract} response at ${path}: expected ${expected}`);
    this.name = "ResponseContractError";
  }
}

export type Decoder<T> = (value: unknown) => T;
export type Validator = (value: unknown, path: string) => void;

class ContractViolation extends Error {
  constructor(
    readonly path: string,
    readonly expected: string,
  ) {
    super(`${path}: ${expected}`);
  }
}

const fail = (path: string, expected: string): never => {
  throw new ContractViolation(path, expected);
};

export const stringValue: Validator = (value, path) => {
  if (typeof value !== "string") fail(path, "string");
};

export const nonEmptyString: Validator = (value, path) => {
  stringValue(value, path);
  if (!(value as string).length) fail(path, "non-empty string");
};

export const numberValue: Validator = (value, path) => {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    fail(path, "finite number");
  }
};

export const integerValue: Validator = (value, path) => {
  numberValue(value, path);
  if (!Number.isInteger(value)) fail(path, "integer");
};

export const booleanValue: Validator = (value, path) => {
  if (typeof value !== "boolean") fail(path, "boolean");
};

export const unknownValue: Validator = () => undefined;

export const recordValue: Validator = (value, path) => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail(path, "object");
  }
};

export const oneOf = (...values: readonly unknown[]): Validator =>
  (value, path) => {
    if (!values.includes(value)) {
      fail(path, values.map(String).join(" | "));
    }
  };

export const optional = (validator: Validator): Validator => (value, path) => {
  if (value !== undefined) validator(value, path);
};

export const nullable = (validator: Validator): Validator => (value, path) => {
  if (value !== null) validator(value, path);
};

export const nullish = (validator: Validator): Validator => (value, path) => {
  if (value !== null && value !== undefined) validator(value, path);
};

export const arrayOf = (validator: Validator): Validator => (value, path) => {
  if (!Array.isArray(value)) fail(path, "array");
  (value as unknown[]).forEach((item, index) => {
    validator(item, `${path}[${index}]`);
  });
};

export const tupleOf = (...validators: Validator[]): Validator =>
  (value, path) => {
    if (!Array.isArray(value) || value.length !== validators.length) {
      fail(path, `${validators.length}-item tuple`);
    }
    validators.forEach((validator, index) => {
      validator((value as unknown[])[index], `${path}[${index}]`);
    });
  };

export const recordOf = (validator: Validator): Validator => (value, path) => {
  recordValue(value, path);
  Object.entries(value as Record<string, unknown>).forEach(([key, item]) => {
    validator(item, `${path}.${key}`);
  });
};

export const objectWith = (
  required: Record<string, Validator>,
  optionalFields: Record<string, Validator> = {},
): Validator => (value, path) => {
  recordValue(value, path);
  const record = value as Record<string, unknown>;
  Object.entries(required).forEach(([key, validator]) => {
    if (!(key in record)) fail(`${path}.${key}`, "present field");
    validator(record[key], `${path}.${key}`);
  });
  Object.entries(optionalFields).forEach(([key, validator]) => {
    if (key in record) validator(record[key], `${path}.${key}`);
  });
};

export const anyOf = (...validators: Validator[]): Validator =>
  (value, path) => {
    for (const validator of validators) {
      try {
        validator(value, path);
        return;
      } catch (error) {
        if (!(error instanceof ContractViolation)) throw error;
      }
    }
    fail(path, "supported variant");
  };

export const decoder = <T>(
  contract: string,
  validator: Validator,
): Decoder<T> => (value) => {
  try {
    validator(value, "$");
    return value as T;
  } catch (error) {
    if (error instanceof ContractViolation) {
      throw new ResponseContractError(
        contract,
        error.path,
        error.expected,
      );
    }
    throw error;
  }
};

export const parseJsonObject = decoder<Record<string, unknown>>(
  "JSON object",
  recordValue,
);

export const parseNoContent = decoder<void>(
  "empty",
  (value, path) => {
    if (value !== null && value !== undefined) fail(path, "empty body");
  },
);
