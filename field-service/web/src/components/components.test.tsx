import { fireEvent, render, screen, within } from "@testing-library/react";
import { setLang } from "@/i18n";
import { DataTable, type Column } from "./DataTable";
import { Drawer, KpiCard, PillTabs } from "./Layout";
import { Amount, FlagChips, Missing, StatusChip } from "./Values";

beforeEach(() => setLang("en"));

describe("values", () => {
  it("shows missing as a dash with the reason, never 0", () => {
    render(<Amount value={null} reason="no elector count" />);
    const dash = screen.getByText("—");
    expect(dash).toHaveAttribute("title", "no elector count");
    expect(screen.queryByText("₹0")).toBeNull();
  });
  it("formats amounts with Indian grouping", () => {
    render(<Amount value="1234567.00" />);
    expect(screen.getByText("₹12,34,567")).toBeInTheDocument();
  });
  it("status carries a label and an icon, not colour alone", () => {
    render(<StatusChip status="pending_ack" />);
    expect(screen.getByText("Awaiting confirmation")).toBeInTheDocument();
    expect(screen.getByText("⏳")).toBeInTheDocument();
  });
  it("flags render as labelled chips", () => {
    render(<FlagChips flags={["large", "ack_overdue"]} />);
    expect(screen.getByText("Large amount")).toBeInTheDocument();
    expect(screen.getByText("Confirmation overdue")).toBeInTheDocument();
  });
  it("speaks Hindi by default", () => {
    setLang("hi");
    render(<Missing />);
    expect(screen.getByText("—")).toHaveAttribute("title", "आँकड़ा उपलब्ध नहीं है (शून्य नहीं)");
  });
});

describe("layout", () => {
  it("KPI card falls back to a dash", () => {
    render(<KpiCard label="Ceiling used" value={null} missingReason="no election window" />);
    expect(screen.getByText("Ceiling used")).toBeInTheDocument();
    expect(screen.getByText("—")).toHaveAttribute("title", "no election window");
  });
  it("pill tabs report the selection", () => {
    const onChange = vi.fn();
    render(<PillTabs label="group" value="day" onChange={onChange}
      items={[{ id: "day", label: "Day" }, { id: "bucket", label: "Bucket" }]} />);
    expect(screen.getByRole("tab", { name: "Day" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("tab", { name: "Bucket" }));
    expect(onChange).toHaveBeenCalledWith("bucket");
  });
  it("drawer closes on Escape", () => {
    const onClose = vi.fn();
    render(<Drawer open title="Entry" onClose={onClose}><p>body</p></Drawer>);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("data table", () => {
  type R = { id: string; name: string; amount: number | null };
  const rows: R[] = [
    { id: "a", name: "Tea", amount: 300 }, { id: "b", name: "Fuel", amount: 1200 },
    { id: "c", name: "Banner", amount: null }, { id: "d", name: "Tea", amount: 50 },
  ];
  const cols: Column<R>[] = [
    { key: "name", header: "Name", render: (r) => r.name, sort: (r) => r.name, filter: (r) => r.name },
    { key: "amount", header: "Amount", align: "right", render: (r) => <Amount value={r.amount} />, sort: (r) => r.amount },
  ];
  const names = () => screen.getAllByRole("row").slice(2).map((tr) => within(tr).getAllByRole("cell")[0].textContent);

  it("sorts on click, flips on a second click, keeps missing last", () => {
    render(<DataTable columns={cols} rows={rows} rowKey={(r) => r.id} />);
    fireEvent.click(screen.getByRole("button", { name: /Amount/ }));
    expect(names()).toEqual(["Tea", "Tea", "Fuel", "Banner"]);
    fireEvent.click(screen.getByRole("button", { name: /Amount/ }));
    expect(names()).toEqual(["Fuel", "Tea", "Tea", "Banner"]);
  });
  it("shift-click adds a second sort", () => {
    render(<DataTable columns={cols} rows={rows} rowKey={(r) => r.id} />);
    fireEvent.click(screen.getByRole("button", { name: /Name/ }));
    fireEvent.click(screen.getByRole("button", { name: /Amount/ }), { shiftKey: true });
    const cells = screen.getAllByRole("row").slice(2).map((tr) => within(tr).getAllByRole("cell").map((c) => c.textContent));
    expect(cells.map((c) => c[0])).toEqual(["Banner", "Fuel", "Tea", "Tea"]);
    expect(cells[2][1]).toBe("₹50");
  });
  it("filters by column and counts rows", () => {
    render(<DataTable columns={cols} rows={rows} rowKey={(r) => r.id} />);
    fireEvent.change(screen.getByLabelText("Filter: Name"), { target: { value: "tea" } });
    expect(names()).toEqual(["Tea", "Tea"]);
    expect(screen.getByText(/2 of 4 rows/)).toBeInTheDocument();
  });
});
