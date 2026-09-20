import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, ErrorPanel, TextInput } from "../components/ui";
import { Logo } from "../components/Logo";
import { Card, CardContent, CardHeader } from "../components/ui/card";
import { ApiError, getErrorMessage, request } from "../lib/api";
import type { RuntimeConfig, Session } from "../lib/types";

interface LoginPageProps {
  runtime: RuntimeConfig;
  onSignedIn: (session: Session) => void;
  initialError?: string;
}

export function LoginPage({ runtime, onSignedIn, initialError }: LoginPageProps) {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(initialError);
  const isFixture = runtime.synthetic || runtime.sourceMode === "fixture";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(undefined);
    try {
      await request("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const session = await request<Session>("/auth/me");
      onSignedIn({
        ...session,
        sourceMode: session.synthetic ? "fixture" : session.sourceMode,
      });
      navigate("/organizations", { replace: true });
    } catch (requestError) {
      const message =
        requestError instanceof ApiError && requestError.status === 401
          ? isFixture
            ? "The demo could not open. Check the demo account and try again."
            : "Timecue could not verify these credentials. Check the account and try again."
          : getErrorMessage(requestError, "Sign-in could not be completed.");
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-5">
      <Card className="w-full max-w-md gap-8 py-8 sm:py-10" aria-labelledby="login-title">
        <CardHeader className="gap-6 px-7 sm:px-10">
          <Logo />
          <div className="flex flex-wrap items-center gap-3">
            <h1 id="login-title">Sign in</h1>
            {isFixture && <Badge tone="fixture">Demo</Badge>}
          </div>
          {!isFixture && (
            <p className="text-muted-foreground">Use your Timecue account.</p>
          )}
        </CardHeader>
        <CardContent className="space-y-6 px-7 sm:px-10">
          {error && <ErrorPanel title="Sign-in unavailable" message={error} />}
          <form className="flex flex-col gap-6" onSubmit={handleSubmit}>
            <TextInput
              label="Email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
            <TextInput
              label="Password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <Button type="submit" className="h-12 w-full" disabled={isSubmitting}>
              {isSubmitting ? "Signing in…" : isFixture ? "Enter demo" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
