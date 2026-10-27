import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MascotLayer } from "./MascotLayer";
import { emitMascot } from "./events";

let desktop = false;
let reducedMotion = false;
const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });
const mascot = () => screen.getByRole("button", { name: "奶蛙" });
const state = () => mascot().getAttribute("data-state");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(2026, 9, 26, 10));
  localStorage.clear();
  localStorage.setItem("xiaowo.mascot.greeting-day", "2026-10-26");
  desktop = false;
  reducedMotion = false;
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("reduced-motion") ? reducedMotion : desktop,
    media: query, onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  }));
  vi.stubGlobal("PointerEvent", MouseEvent);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("奶蛙陪伴层", () => {
  it("starts idle, laughs on click for 1.5 seconds, then returns to idle", () => {
    render(<MascotLayer />);
    expect(state()).toBe("idle");
    fireEvent.click(mascot());
    expect(state()).toBe("laugh");
    expect(mascot().querySelector("img")).toHaveAttribute("src", expect.stringContaining("laugh-original.gif"));
    expect(mascot()).toHaveAttribute("data-animated-image", "true");
    advance(1499); expect(state()).toBe("laugh");
    advance(1); expect(state()).toBe("idle");
    expect(mascot().querySelector("img")).toHaveAttribute("src", expect.stringContaining("idle.webp"));
  });

  it("falls asleep only after 60 seconds of inactivity and wakes on interaction", () => {
    render(<MascotLayer />);
    advance(59000);
    fireEvent.keyDown(window, { key: "a" });
    advance(59999); expect(state()).toBe("idle");
    advance(1); expect(state()).toBe("sleep");
    fireEvent.click(mascot()); expect(state()).toBe("laugh");
  });

  it("keeps a pending question thinking through inactivity and temporary laughter", () => {
    render(<MascotLayer />);
    act(() => emitMascot("think"));
    advance(60000); expect(state()).toBe("think");
    fireEvent.click(mascot()); expect(state()).toBe("laugh");
    advance(1500); expect(state()).toBe("think");
    act(() => emitMascot("success")); expect(state()).toBe("celebrate");
    advance(3000); expect(state()).toBe("idle");
  });

  it("cancels prior reaction timers on new requests and handles errors and stop", () => {
    render(<MascotLayer />);
    fireEvent.click(mascot()); advance(500);
    act(() => emitMascot("think"));
    advance(1500); expect(state()).toBe("think");
    act(() => emitMascot("error", { message: "先连接教务，再一起查" }));
    expect(state()).toBe("surprised");
    expect(screen.getByText("先连接教务，再一起查")).toBeInTheDocument();
    act(() => emitMascot("think"));
    act(() => emitMascot("idle"));
    expect(state()).toBe("idle");
    advance(3000); expect(state()).toBe("idle");
  });

  it("keeps an idle guidance message and restores the default on a plain stop", () => {
    render(<MascotLayer />);
    act(() => emitMascot("think"));
    act(() => emitMascot("idle", { message: "连接学校账号后，一起查课表" }));
    expect(state()).toBe("idle");
    expect(screen.getByText("连接学校账号后，一起查课表")).toBeInTheDocument();
    act(() => emitMascot("idle"));
    expect(screen.getByText("我在这儿，陪你慢慢来。")).toBeInTheDocument();
  });

  it("long press increases affection once without also triggering laughter", () => {
    render(<MascotLayer />);
    fireEvent.pointerDown(mascot(), { button: 0, clientX: 20, clientY: 20 });
    advance(500);
    expect(screen.getByText("摸摸头，好感度 +1")).toBeInTheDocument();
    fireEvent.pointerUp(mascot());
    fireEvent.click(mascot());
    expect(state()).toBe("idle");
    expect(localStorage.getItem("xiaowo.mascot.affection")).toBe("1");
  });

  it("desktop drag returns to its corner and does not count as petting or clicking", () => {
    desktop = true;
    render(<MascotLayer />);
    fireEvent.pointerDown(mascot(), { button: 0, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(mascot(), { clientX: 80, clientY: 70 });
    advance(500);
    expect(mascot().closest(".mascot-motion")).toHaveStyle({ transform: "translate(60px, 50px)" });
    fireEvent.pointerUp(mascot());
    fireEvent.click(mascot());
    expect(mascot().closest(".mascot-motion")).toHaveStyle({ transform: "translate(0px, 0px)" });
    expect(state()).toBe("idle");
    expect(localStorage.getItem("xiaowo.mascot.affection")).toBeNull();
  });

  it("keeps the mobile mascot in flow and does not turn a swipe into a pet", () => {
    render(<MascotLayer />);
    expect(screen.getByLabelText("奶蛙陪伴")).toHaveAttribute("data-placement", "inline");
    fireEvent.pointerDown(mascot(), { button: 0, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(mascot(), { clientX: 20, clientY: 80 });
    advance(600);
    fireEvent.pointerUp(mascot()); fireEvent.click(mascot());
    expect(state()).toBe("idle");
    expect(localStorage.getItem("xiaowo.mascot.affection")).toBeNull();
  });

  it("uses a static reaction when reduced motion is requested", () => {
    reducedMotion = true;
    render(<MascotLayer />);
    fireEvent.click(mascot());
    expect(state()).toBe("laugh");
    expect(screen.getByLabelText("奶蛙陪伴")).toHaveAttribute("data-reduced-motion", "true");
    expect(mascot().querySelector("img")).toHaveAttribute("src", expect.stringContaining("laugh.webp"));
    expect(mascot()).toHaveAttribute("data-animated-image", "false");
    advance(1500); expect(state()).toBe("idle");
  });

  it("falls back to the static laugh when the GIF fails without interrupting the pending question", () => {
    render(<MascotLayer />);
    act(() => emitMascot("think"));
    fireEvent.click(mascot());
    const image = mascot().querySelector("img")!;
    expect(image).toHaveAttribute("src", expect.stringContaining("laugh-original.gif"));
    fireEvent.error(image);
    expect(image).toHaveAttribute("src", expect.stringContaining("laugh.webp"));
    expect(mascot()).toHaveAttribute("data-animated-image", "false");
    expect(state()).toBe("laugh");
    advance(1500);
    expect(state()).toBe("think");
    fireEvent.click(mascot());
    expect(mascot().querySelector("img")).toHaveAttribute("src", expect.stringContaining("laugh.webp"));
  });

  it("does not interrupt an early answer or failure with the daily greeting", () => {
    localStorage.removeItem("xiaowo.mascot.greeting-day");
    render(<MascotLayer />);
    act(() => emitMascot("error"));
    advance(500);
    expect(state()).toBe("surprised");
    expect(localStorage.getItem("xiaowo.mascot.greeting-day")).toBeNull();
  });

  it("greets only once per local day and supports the eating easter egg", () => {
    localStorage.removeItem("xiaowo.mascot.greeting-day");
    const view = render(<MascotLayer />);
    expect(state()).toBe("idle");
    advance(500);
    expect(state()).toBe("laugh");
    expect(screen.getByText("早上好呀！今天也一起慢慢来。")).toBeInTheDocument();
    view.unmount();
    render(<MascotLayer />); advance(500); expect(state()).toBe("idle");
    act(() => emitMascot("eat")); expect(state()).toBe("eat");
    advance(3000); expect(state()).toBe("idle");
  });
});
