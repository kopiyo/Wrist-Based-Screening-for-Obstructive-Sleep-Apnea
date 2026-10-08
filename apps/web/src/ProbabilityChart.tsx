import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Point = { minute: number; probability: number };

export default function ProbabilityChart({ data }: { data: Point[] }) {
  return <div className="chart-area" role="img" aria-label="Minute by minute screening probability chart">
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--line)" />
        <XAxis dataKey="minute" tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} label={{ value: "Minute", position: "insideBottom", offset: -1, fill: "var(--muted)" }} />
        <YAxis domain={[0, 100]} tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} tickFormatter={value => `${value}%`} />
        <Tooltip formatter={value => [`${value}%`, "Probability"]} labelFormatter={value => `Minute ${value}`} />
        <Area type="monotone" dataKey="probability" stroke="#0B6E6E" strokeWidth={2} fill="#0B6E6E" fillOpacity={0.07} />
      </AreaChart>
    </ResponsiveContainer>
  </div>;
}
