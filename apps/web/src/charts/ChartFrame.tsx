import { useRef } from "react";
import type { ReactNode, RefObject } from "react";
import { ChartMode } from "./types";

export function ChartFrame<T>({ mode, title, children, table }: {
  mode: ChartMode<T>;
  title: string;
  children: ReactNode;
  table?: ReactNode;
}) {
  return <section className="p2-chart" aria-label={title}>
    {mode.kind === "demo" && <p className="p2-demo-notice" role="note">{mode.disclosure}</p>}
    {children}
    {table && <details className="p2-chart-table"><summary>View chart data table</summary>{table}</details>}
  </section>;
}

export function SourceBadge({ validated }: { validated: boolean }) {
  return <span className={`p2-source-badge ${validated ? "is-validated" : "is-unvalidated"}`}>
    {validated ? "Validated source" : "Unvalidated source"}
  </span>;
}

export function ModelMeta({ sourceValidated, modelVersion }: { sourceValidated: boolean; modelVersion: string }) {
  return <div className="p2-chart-meta"><SourceBadge validated={sourceValidated} /><span>Model {modelVersion}</span></div>;
}

const EXPORTED_STYLES = ["fill", "fill-opacity", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
  "stroke-linejoin", "stroke-opacity", "opacity", "font-family", "font-size", "font-weight"];

export function ChartExports({ svgRef, fileBaseName }: { svgRef: RefObject<SVGSVGElement | null>; fileBaseName: string }) {
  const safeName = fileBaseName.replace(/[^a-z0-9_-]+/gi, "-").replace(/^-|-$/g, "") || "chart";
  function serialize() {
    const svg = svgRef.current;
    if (!svg) return null;
    const copy = svg.cloneNode(true) as SVGSVGElement;
    if (!copy.getAttribute("xmlns")) copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    const width = svg.viewBox.baseVal.width || svg.clientWidth || 900;
    const height = svg.viewBox.baseVal.height || svg.clientHeight || 320;
    copy.setAttribute("width", String(width));
    copy.setAttribute("height", String(height));
    // Chart styling lives in charts.css, which a standalone file cannot see: inline the computed values.
    const source = [svg, ...Array.from(svg.querySelectorAll<SVGElement>("*"))];
    const target = [copy, ...Array.from(copy.querySelectorAll<SVGElement>("*"))];
    source.forEach((element, index) => {
      const computed = getComputedStyle(element);
      const style = EXPORTED_STYLES.map(name => `${name}:${computed.getPropertyValue(name)}`).join(";");
      target[index].setAttribute("style", style);
    });
    const background = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    background.setAttribute("width", "100%"); background.setAttribute("height", "100%");
    background.setAttribute("fill", getComputedStyle(svg).getPropertyValue("--p2-chart-bg").trim() || "#ffffff");
    copy.insertBefore(background, copy.firstChild);
    return new XMLSerializer().serializeToString(copy);
  }
  function downloadSvg() {
    const content = serialize(); if (!content) return;
    download(new Blob([content], { type: "image/svg+xml;charset=utf-8" }), `${safeName}.svg`);
  }
  async function downloadPng() {
    const content = serialize(); if (!content) return;
    const blob = new Blob([content], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    try {
      const image = new Image();
      image.src = url;
      await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(new Error("Chart could not be rendered for export.")); });
      const svg = svgRef.current;
      const scale = 2;
      const width = (svg?.viewBox.baseVal.width || svg?.clientWidth || 900) * scale;
      const height = (svg?.viewBox.baseVal.height || svg?.clientHeight || 320) * scale;
      const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
      const context = canvas.getContext("2d"); if (!context) return;
      context.drawImage(image, 0, 0, width, height);
      canvas.toBlob((png) => { if (png) download(png, `${safeName}.png`); }, "image/png");
    } finally { URL.revokeObjectURL(url); }
  }
  return <div className="p2-chart-export" role="group" aria-label="Export chart">
    <button type="button" onClick={downloadSvg}>SVG</button>
    <button type="button" onClick={() => { void downloadPng(); }}>PNG</button>
  </div>;
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url);
}

export function useChartSvgRef() { return useRef<SVGSVGElement>(null); }

export function DataTable({ caption, columns, rows }: { caption: string; columns: string[]; rows: (string | number)[][] }) {
  return <div className="p2-table-scroll"><table><caption>{caption}</caption><thead><tr>{columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>)}</tbody></table></div>;
}

/** Elapsed seconds as h:mm, the natural unit for an overnight recording. */
export function formatElapsed(seconds: number) {
  const total = Math.max(0, Math.round(seconds / 60));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function timeTicks(max: number, count = 5) {
  return Array.from({ length: count }, (_, i) => max * i / (count - 1));
}
