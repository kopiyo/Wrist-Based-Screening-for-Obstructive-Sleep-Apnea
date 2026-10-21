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

export function ChartExports({ svgRef, fileBaseName }: { svgRef: RefObject<SVGSVGElement | null>; fileBaseName: string }) {
  const safeName = fileBaseName.replace(/[^a-z0-9_-]+/gi, "-").replace(/^-|-$/g, "") || "chart";
  function serialize() {
    const svg = svgRef.current;
    if (!svg) return null;
    const copy = svg.cloneNode(true) as SVGSVGElement;
    if (!copy.getAttribute("xmlns")) copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    copy.setAttribute("width", String(svg.viewBox.baseVal.width || svg.clientWidth || 900));
    copy.setAttribute("height", String(svg.viewBox.baseVal.height || svg.clientHeight || 320));
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
      context.fillStyle = getComputedStyle(svg ?? document.body).getPropertyValue("--p2-chart-bg").trim() || "#ffffff";
      context.fillRect(0, 0, width, height); context.drawImage(image, 0, 0, width, height);
      canvas.toBlob((png) => { if (png) download(png, `${safeName}.png`); }, "image/png");
    } finally { URL.revokeObjectURL(url); }
  }
  return <div className="p2-chart-export" aria-label="Export chart">
    <button type="button" onClick={downloadSvg}>Download SVG</button>
    <button type="button" onClick={() => { void downloadPng(); }}>Download PNG</button>
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
