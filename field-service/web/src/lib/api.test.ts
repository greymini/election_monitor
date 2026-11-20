import { renderHook } from "@testing-library/react";
import { useIdempotencyKey } from "./api";

describe("idempotency keys for web creates", () => {
  it("a retry of the same request keeps its key, so the server replays instead of saving twice", () => {
    const { result } = renderHook(() => useIdempotencyKey());
    const body = { kind: "expense", amount: "1500" };
    const first = result.current.keyFor(body);
    expect(result.current.keyFor({ ...body })).toBe(first);
  });

  it("a changed request gets a new key; so does the next request after a success", () => {
    const { result } = renderHook(() => useIdempotencyKey());
    const a = result.current.keyFor({ amount: "1500" });
    const b = result.current.keyFor({ amount: "1600" });
    expect(b).not.toBe(a);
    result.current.done();
    expect(result.current.keyFor({ amount: "1600" })).not.toBe(b);
  });
});
