import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { LoadingBlock } from "@/components/common";
import { AppLayout } from "@/components/layout/AppLayout";
import { AuthProvider, useAuth } from "@/lib/auth";
import LoginPage, { RegisterPage } from "@/pages/Login";

const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Forecasting = lazy(() => import("@/pages/Forecasting"));
const Inventory = lazy(() => import("@/pages/Inventory"));
const Suppliers = lazy(() => import("@/pages/Suppliers"));
const Logistics = lazy(() => import("@/pages/Logistics"));
const Scenarios = lazy(() => import("@/pages/Scenarios"));
const Reports = lazy(() => import("@/pages/Reports"));
const DataManagement = lazy(() => import("@/pages/DataManagement"));
const SettingsPage = lazy(() => import("@/pages/Settings"));

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="p-8"><LoadingBlock /></div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export function AppRoutes() {
  return (
    <Suspense fallback={<div className="p-8"><LoadingBlock /></div>}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route element={<RequireAuth><AppLayout /></RequireAuth>}>
          <Route index element={<Dashboard />} />
          <Route path="forecasting" element={<Forecasting />} />
          <Route path="inventory" element={<Inventory />} />
          <Route path="suppliers" element={<Suppliers />} />
          <Route path="logistics" element={<Logistics />} />
          <Route path="scenarios" element={<Scenarios />} />
          <Route path="reports" element={<Reports />} />
          <Route path="data" element={<DataManagement />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="py-20 text-center text-sm text-muted-foreground">Page not found.</div>} />
        </Route>
      </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
