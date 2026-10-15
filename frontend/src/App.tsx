import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Models from "./pages/Models";
import Batches from "./pages/Batches";
import BatchDetail from "./pages/BatchDetail";
import LoadTest from "./pages/LoadTest";
import Reports from "./pages/Reports";
import ReportDetail from "./pages/ReportDetail";
import { useLiveStreamConnection } from "./useLiveStream";

const links = [
  ["/", "Dashboard"],
  ["/models", "Models"],
  ["/batches", "Batches"],
  ["/load-test", "Load test"],
  ["/reports", "Reports"],
] as const;

export default function App() {
  useLiveStreamConnection();
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-6 px-4 py-3">
          <span className="font-semibold">Inference Gateway</span>
          <nav className="flex flex-wrap gap-1">
            {links.map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm ${isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/models" element={<Models />} />
          <Route path="/batches" element={<Batches />} />
          <Route path="/batches/:batchId" element={<BatchDetail />} />
          <Route path="/load-test" element={<LoadTest />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/reports/:runId" element={<ReportDetail />} />
        </Routes>
      </main>
    </div>
  );
}
