import { addDays, inr, inrShort, num, pct, toNumber } from "./format";

describe("Indian number formatting", () => {
  it("groups digits the Indian way", () => {
    expect(num(304898)).toBe("3,04,898");
    expect(num("12345678")).toBe("1,23,45,678");
    expect(num(999)).toBe("999");
  });
  it("formats money from decimal strings, paise only when present", () => {
    expect(inr("250000.00")).toBe("₹2,50,000");
    expect(inr("1234.50")).toBe("₹1,234.50");
    expect(inr("0")).toBe("₹0");
    expect(inr("-1500.50")).toBe("−₹1,500.50");                         // sign before the rupee sign
  });
  it("keeps missing as null, never 0", () => {
    expect(inr(null)).toBeNull();
    expect(num(undefined)).toBeNull();
    expect(pct("")).toBeNull();
    expect(toNumber("abc")).toBeNull();
  });
  it("shortens for axes in both languages", () => {
    expect(inrShort(250000, "en")).toBe("₹2.5L");
    expect(inrShort(12_000_000, "en")).toBe("₹1.2Cr");
    expect(inrShort(250000, "hi")).toBe("₹2.5 लाख");
    expect(inrShort(950, "en")).toBe("₹950");
  });
  it("percent and date arithmetic", () => {
    expect(pct("12.345")).toBe("12.3%");
    expect(addDays("2026-02-28", 1)).toBe("2026-03-01");
  });
});
