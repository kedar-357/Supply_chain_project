import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { Spinner } from "@/components/common";
import { Logo } from "@/components/layout/AppLayout";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function validateLogin(email: string, password: string): Record<string, string> {
  const e: Record<string, string> = {};
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) e.email = "Enter a valid email address";
  if (!password) e.password = "Password is required";
  return e;
}

export function validateRegister(name: string, email: string, password: string, confirm: string): Record<string, string> {
  const e: Record<string, string> = {};
  if (name.trim().length < 2) e.name = "Enter your full name";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) e.email = "Enter a valid email address";
  if (password.length < 8) e.password = "At least 8 characters";
  else if (!/\d/.test(password) || !/[a-zA-Z]/.test(password)) e.password = "Use letters and digits";
  if (confirm !== password) e.confirm = "Passwords do not match";
  return e;
}

function AuthShell({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="hidden flex-col justify-between bg-navy-800 p-10 text-slate-300 lg:flex">
        <Logo />
        <div className="max-w-md">
          <h2 className="text-2xl font-semibold text-white">Predict demand. Optimize inventory. Decide with confidence.</h2>
          <ul className="mt-6 space-y-2 text-sm">
            <li>• ML demand forecasting with honest holdout evaluation</li>
            <li>• Safety stock, reorder points, EOQ and LP-based order optimization</li>
            <li>• Supplier scoring and delay-risk prediction</li>
            <li>• Network routing and what-if scenario simulation</li>
          </ul>
        </div>
        <p className="text-xs text-slate-500">B.Tech major project · SupplyChainX</p>
      </div>
      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Logo dark={false} />
          </div>
          <h1 className="text-xl font-semibold">{title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
          <div className="mt-6">{children}</div>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation() as { state?: { from?: string } };
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={location.state?.from ?? "/"} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const v = validateLogin(email, password);
    setErrors(v);
    if (Object.keys(v).length) return;
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(location.state?.from ?? "/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell title="Sign in" subtitle="Access the SupplyChainX decision-support platform.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <Alert variant="error">{error}</Alert>}
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={!!errors.email} />
          {errors.email && <p className="text-xs text-red-700">{errors.email}</p>}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} aria-invalid={!!errors.password} />
          {errors.password && <p className="text-xs text-red-700">{errors.password}</p>}
        </div>
        <Button type="submit" className="w-full" disabled={busy}>
          {busy && <Spinner />} Sign in
        </Button>
        <p className="text-center text-xs text-muted-foreground">
          No account? <Link to="/register" className="font-medium text-primary hover:underline">Register as analyst</Link>
        </p>
        <p className="rounded-md bg-secondary p-2.5 text-[11px] text-muted-foreground">
          Demo accounts (admin / manager / analyst @supplychainx.demo) are created by the seed script; their passwords are in
          <code className="mx-1">backend/.env</code>.
        </p>
      </form>
    </AuthShell>
  );
}

export function RegisterPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [f, setF] = useState({ name: "", email: "", password: "", confirm: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const v = validateRegister(f.name, f.email, f.password, f.confirm);
    setErrors(v);
    if (Object.keys(v).length) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/register", { full_name: f.name, email: f.email, password: f.password });
      await login(f.email, f.password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  const field = (k: keyof typeof f, label: string, type = "text", auto?: string) => (
    <div className="space-y-1.5">
      <Label htmlFor={k}>{label}</Label>
      <Input id={k} type={type} autoComplete={auto} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} aria-invalid={!!errors[k]} />
      {errors[k] && <p className="text-xs text-red-700">{errors[k]}</p>}
    </div>
  );
  return (
    <AuthShell title="Create an account" subtitle="New accounts get the Analyst role; an administrator can grant more access.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <Alert variant="error">{error}</Alert>}
        {field("name", "Full name", "text", "name")}
        {field("email", "Email", "email", "email")}
        {field("password", "Password", "password", "new-password")}
        {field("confirm", "Confirm password", "password", "new-password")}
        <Button type="submit" className="w-full" disabled={busy}>
          {busy && <Spinner />} Create account
        </Button>
        <p className="text-center text-xs text-muted-foreground">
          Already registered? <Link to="/login" className="font-medium text-primary hover:underline">Sign in</Link>
        </p>
      </form>
    </AuthShell>
  );
}
