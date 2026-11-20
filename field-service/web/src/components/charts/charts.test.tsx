import { fireEvent, render, screen } from "@testing-library/react";
import { setLang } from "@/i18n";
import { ChartFrame, HBars, ValueLegend } from "./index";

beforeEach(() => setLang("en"));

const items = [
  { key: "a", label: "Meetings", value: 30000, color: "var(--em-cat-1)" },
  { key: "b", label: "Travel", value: 10000, color: "var(--em-cat-2)" },
];

describe("chart wrappers", () => {
  it("legend carries every value and share, so colour is never the only signal", () => {
    render(<ValueLegend items={items} total={40000} />);
    expect(screen.getByText("Meetings")).toBeInTheDocument();
    expect(screen.getByText("₹30,000")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
  });

  it("legend items can select (drill-down)", () => {
    const onSelect = vi.fn();
    render(<ValueLegend items={items} total={40000} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /Travel/ }));
    expect(onSelect).toHaveBeenCalledWith("b");
  });

  it("every chart has a table view of the same numbers", () => {
    render(<ChartFrame table={<table><tbody><tr><td>table body</td></tr></tbody></table>}><div>the chart</div></ChartFrame>);
    expect(screen.getByText("the chart")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show table" }));
    expect(screen.getByText("table body")).toBeInTheDocument();
    expect(screen.queryByText("the chart")).toBeNull();
  });

  it("horizontal bars are labelled directly", () => {
    render(<HBars items={[{ key: "x", label: "Demo area", value: 1200, sub: "3 entries" }]} />);
    expect(screen.getByText("Demo area")).toBeInTheDocument();
    expect(screen.getByText("₹1,200")).toBeInTheDocument();
    expect(screen.getByText("3 entries")).toBeInTheDocument();
  });
});
